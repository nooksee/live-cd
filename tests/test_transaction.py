from __future__ import annotations

import os
import stat
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from liveusb import messages
from liveusb.backend import Context
from liveusb.backend import chroot
from liveusb.backend import transaction as transaction_module
from liveusb.backend.transaction import (
    ChrootTransaction,
    TransactionCleanupError,
)


def _node_snapshot(path):
    path = str(path)
    if not os.path.lexists(path):
        return ("absent",)

    stat_result = os.lstat(path)
    identity = (stat_result.st_dev, stat_result.st_ino)
    mode = stat.S_IMODE(stat_result.st_mode)
    if stat.S_ISLNK(stat_result.st_mode):
        return ("symlink", identity, mode, os.readlink(path))
    if stat.S_ISREG(stat_result.st_mode):
        with open(path, "rb") as file_handle:
            content = file_handle.read()
        return ("file", identity, mode, content)
    return ("other", identity, mode)


class _FakeChrootRunner:
    def __init__(
        self,
        fs_dir,
        requested_command,
        requested_result=None,
        requested_error=None,
        requested_callback=None,
    ):
        self.fs_dir = Path(fs_dir)
        self.requested_command = list(requested_command)
        self.requested_result = (
            SimpleNamespace(returncode=0)
            if requested_result is None
            else requested_result
        )
        self.requested_error = requested_error
        self.requested_callback = requested_callback
        self.commands = []

    @property
    def payloads(self):
        return [command[7:] for command in self.commands]

    def __call__(self, command):
        command = list(command)
        self.commands.append(command)
        payload = command[7:]

        if payload[:3] == ["ln", "-s", "/bin/true"]:
            target = self.fs_dir / payload[3].lstrip("/")
            os.symlink("/bin/true", str(target))
            return SimpleNamespace(returncode=0)

        if payload == self.requested_command:
            if self.requested_callback is not None:
                self.requested_callback()
            if self.requested_error is not None:
                raise self.requested_error
            return self.requested_result

        return SimpleNamespace(returncode=0)


class ChrootTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.work_dir = self.root / "work"
        self.fs_dir = self.work_dir / "FileSystem"
        self.mount_dir = self.root / "mount"
        self.host_dir = self.root / "host"

        for directory in (
            self.fs_dir / "etc/init.d",
            self.fs_dir / "sbin",
            self.fs_dir / "usr/sbin",
            self.fs_dir / "tmp",
            self.host_dir,
            self.mount_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.hosts_source = self.host_dir / "hosts"
        self.resolv_source = self.host_dir / "resolv.conf"
        self._write_file(self.hosts_source, b"127.0.0.1 test-host\n")
        self._write_file(
            self.resolv_source,
            b"nameserver 192.0.2.53\n",
        )
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.mount_dir),
            apt_helper=False,
            locales="C.UTF-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _write_file(path, content, mode=0o644):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.chmod(str(path), mode)

    def _managed_paths(self):
        return tuple(
            self.fs_dir / "etc" / name
            for name in (
                "hosts",
                "resolv.conf",
                "debian_chroot",
                "mtab",
            )
        )

    def _seed_managed_nodes(self):
        hosts, resolv_conf, debian_chroot, mtab = self._managed_paths()
        self._write_file(hosts, b"legacy hosts\n", 0o640)

        resolv_target = self.fs_dir / "etc/resolver-origin"
        self._write_file(resolv_target, b"legacy resolver\n", 0o600)
        os.symlink("resolver-origin", str(resolv_conf))

        self._write_file(
            debian_chroot,
            b"legacy marker\n",
            0o604,
        )
        os.symlink("/legacy/mounts", str(mtab))

    def _seed_service(self, relative_path, content=b"legacy service\n"):
        path = self.fs_dir / relative_path
        self._write_file(path, content, 0o755)
        return path

    def _transaction(self):
        return ChrootTransaction(
            self.ctx,
            {
                "hosts": str(self.hosts_source),
                "resolv.conf": str(self.resolv_source),
            },
        )

    @contextmanager
    def _patched_chroot(self, runner):
        with mock.patch.object(
            chroot,
            "run",
            side_effect=runner,
        ), mock.patch.object(
            transaction_module,
            "HOSTS_SOURCE",
            str(self.hosts_source),
        ), mock.patch.object(
            transaction_module,
            "RESOLV_CONF_SOURCE",
            str(self.resolv_source),
        ), mock.patch.object(
            messages,
            "info",
        ):
            yield

    def _assert_no_transaction_residue(self):
        self.assertFalse(
            os.path.lexists(str(self.fs_dir / "tmp/lock_chroot"))
        )
        backups = tuple(
            self.fs_dir.rglob("*.liveusb-transaction-*")
        )
        self.assertEqual(backups, tuple())

    def test_success_restores_exact_managed_nodes_and_records_services(self):
        self._seed_managed_nodes()
        first_service = self._seed_service("sbin/initctl")
        second_service = self._seed_service(
            "etc/init.d/example-service",
        )
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        services_before = {
            path: _node_snapshot(path)
            for path in (first_service, second_service)
        }
        transaction = self._transaction()

        with transaction:
            self.assertEqual(
                (self.fs_dir / "etc/hosts").read_bytes(),
                self.hosts_source.read_bytes(),
            )
            self.assertEqual(
                (self.fs_dir / "etc/resolv.conf").read_bytes(),
                self.resolv_source.read_bytes(),
            )
            self.assertEqual(
                (self.fs_dir / "etc/debian_chroot").read_text(
                    encoding="utf-8"
                ),
                "chroot\n",
            )
            self.assertEqual(
                os.readlink(str(self.fs_dir / "etc/mtab")),
                "/proc/mounts",
            )
            blocked = transaction.block_files(
                (
                    str(first_service),
                    str(self.fs_dir / "sbin/absent"),
                    str(second_service),
                )
            )
            self.assertEqual(
                blocked,
                (str(first_service), str(second_service)),
            )

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        for path, snapshot in services_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
            self.assertFalse(os.path.lexists(str(path) + ".blocked"))
        self._assert_no_transaction_residue()

    def test_originally_absent_managed_nodes_return_to_absence(self):
        for path in self._managed_paths():
            self.assertEqual(_node_snapshot(path), ("absent",))

        with self._transaction():
            for path in self._managed_paths():
                self.assertNotEqual(_node_snapshot(path), ("absent",))

        for path in self._managed_paths():
            self.assertEqual(_node_snapshot(path), ("absent",))
        self._assert_no_transaction_residue()

    def test_existing_lock_is_rejected_without_mutation(self):
        self._seed_managed_nodes()
        lock_path = self.fs_dir / "tmp/lock_chroot"
        self._write_file(lock_path, b"existing lock\n", 0o600)
        lock_before = _node_snapshot(lock_path)
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }

        with self.assertRaisesRegex(
            messages.LiveUSBError,
            "FileSystem is locked",
        ):
            with self._transaction():
                self.fail("An existing lock must prevent entry")

        self.assertEqual(_node_snapshot(lock_path), lock_before)
        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)

    def test_forced_stale_lock_takeover_leaves_no_lock(self):
        lock_path = self.fs_dir / "tmp/lock_chroot"
        self._write_file(lock_path, b"stale lock\n", 0o600)
        self.ctx.force_chroot = True

        with self._transaction():
            self.assertTrue(os.path.lexists(str(lock_path)))
            self.assertNotEqual(lock_path.read_bytes(), b"stale lock\n")
            self.assertEqual(len(lock_path.read_bytes()), 32)

        self._assert_no_transaction_residue()

    def test_setup_failure_after_partial_mutation_restores_every_node(self):
        self._seed_managed_nodes()
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        real_copyfile = transaction_module.shutil.copyfile
        copy_count = 0

        def fail_second_copy(source, destination):
            nonlocal copy_count
            copy_count += 1
            if copy_count == 2:
                raise OSError("simulated resolver copy failure")
            return real_copyfile(source, destination)

        with mock.patch.object(
            transaction_module.shutil,
            "copyfile",
            side_effect=fail_second_copy,
        ):
            with self.assertRaisesRegex(
                OSError,
                "simulated resolver copy failure",
            ):
                with self._transaction():
                    self.fail("Setup failure must prevent entry")

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self._assert_no_transaction_residue()

    def test_stub_failure_after_rename_restores_blocked_file(self):
        service = self._seed_service("sbin/initctl")
        service_before = _node_snapshot(service)

        def failing_stub_creator(path):
            os.symlink("/bin/true", path)
            raise OSError("simulated stub failure")

        with self.assertRaisesRegex(
            OSError,
            "simulated stub failure",
        ):
            with self._transaction() as transaction:
                transaction.block_files(
                    (str(service),),
                    failing_stub_creator,
                )

        self.assertEqual(_node_snapshot(service), service_before)
        self.assertFalse(os.path.lexists(str(service) + ".blocked"))
        self._assert_no_transaction_residue()

    def test_chroot_command_exception_restores_state_and_runs_cleanup(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        service_before = _node_snapshot(service)
        primary_error = RuntimeError("simulated command failure")
        requested = ["custom-command", "--argument"]
        runner = _FakeChrootRunner(
            self.fs_dir,
            requested,
            requested_error=primary_error,
        )

        with self._patched_chroot(runner):
            with self.assertRaises(RuntimeError) as captured:
                chroot.chroot_run(self.ctx, *requested)

        self.assertIs(captured.exception, primary_error)
        self.assertEqual(
            runner.payloads,
            [
                ["locale-gen", self.ctx.locales],
                ["ln", "-s", "/bin/true", "/sbin/initctl"],
                requested,
                ["apt-get", "autoremove", "--purge"],
                ["apt-get", "autoclean"],
                ["apt-get", "clean"],
            ],
        )
        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_cleanup_failure_attempts_remaining_commands_and_chains_primary(
        self,
    ):
        self._seed_managed_nodes()
        self._seed_service("sbin/initctl")
        requested = ["custom-command"]
        primary_error = RuntimeError("simulated command failure")
        runner = _FakeChrootRunner(
            self.fs_dir,
            requested,
            requested_error=primary_error,
        )
        failing_cleanup = ["apt-get", "autoremove", "--purge"]

        def fail_one_cleanup_command(command):
            if list(command)[7:] == failing_cleanup:
                runner.commands.append(list(command))
                raise OSError("simulated cleanup command failure")
            return runner(command)

        with self._patched_chroot(fail_one_cleanup_command):
            with self.assertRaises(TransactionCleanupError) as captured:
                chroot.chroot_run(self.ctx, *requested)

        self.assertIs(captured.exception.__cause__, primary_error)
        self.assertEqual(
            [
                (failure.operation, failure.path)
                for failure in captured.exception.failures
            ],
            [
                (
                    "chroot_cleanup_command",
                    " ".join(runner.commands[-3]),
                )
            ],
        )
        self.assertEqual(
            runner.payloads[-3:],
            [
                failing_cleanup,
                ["apt-get", "autoclean"],
                ["apt-get", "clean"],
            ],
        )
        self._assert_no_transaction_residue()

    def test_nonzero_command_result_is_returned_and_warned(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        requested = ["custom-command"]
        expected_result = SimpleNamespace(returncode=23)
        runner = _FakeChrootRunner(
            self.fs_dir,
            requested,
            requested_result=expected_result,
        )

        with self._patched_chroot(runner), mock.patch.object(
            messages,
            "warning",
        ) as warning:
            result = chroot.chroot_run(self.ctx, *requested)

        self.assertIs(result, expected_result)
        warning.assert_any_call("chroot has returned exit status")
        self.assertEqual(_node_snapshot(service)[0], "file")
        self._assert_no_transaction_residue()

    def test_partial_unblock_failure_does_not_stop_other_cleanup(self):
        self._seed_managed_nodes()
        first_service = self._seed_service("sbin/initctl")
        second_service = self._seed_service(
            "etc/init.d/example-service",
        )
        services_before = {
            path: _node_snapshot(path)
            for path in (first_service, second_service)
        }
        transaction = self._transaction()
        transaction.__enter__()
        transaction.block_files(
            (str(first_service), str(second_service))
        )
        real_rename = transaction_module.os.rename

        def fail_first_restore(source, destination):
            if (
                source == str(first_service) + ".blocked"
                and destination == str(first_service)
            ):
                raise OSError("simulated first restore failure")
            return real_rename(source, destination)

        with mock.patch.object(
            transaction_module.os,
            "rename",
            side_effect=fail_first_restore,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                transaction.cleanup()

        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "restore_blocked_file",
        )
        self.assertEqual(
            captured.exception.failures[0].path,
            str(first_service),
        )
        self.assertEqual(
            _node_snapshot(second_service),
            services_before[second_service],
        )
        self.assertFalse(
            os.path.lexists(
                str(second_service) + ".blocked"
            )
        )
        self.assertFalse(
            os.path.lexists(str(self.fs_dir / "tmp/lock_chroot"))
        )

        transaction.cleanup()
        for path, snapshot in services_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self._assert_no_transaction_residue()

    def test_replacement_installed_during_command_is_preserved(self):
        service = self._seed_service("sbin/initctl")

        with mock.patch.object(messages, "warning") as warning:
            with self._transaction() as transaction:
                transaction.block_files((str(service),))
                os.unlink(str(service))
                self._write_file(
                    service,
                    b"replacement installed in chroot\n",
                    0o755,
                )

        self.assertEqual(
            service.read_bytes(),
            b"replacement installed in chroot\n",
        )
        warning.assert_called_once_with(
            f"{service} has been updated, removing blocked file!"
        )
        self.assertFalse(os.path.lexists(str(service) + ".blocked"))
        self._assert_no_transaction_residue()

    def test_multiple_cleanup_failures_are_ordered_and_chain_primary(self):
        self._seed_managed_nodes()
        first_service = self._seed_service("sbin/initctl")
        second_service = self._seed_service(
            "etc/init.d/example-service",
        )
        services_before = {
            path: _node_snapshot(path)
            for path in (first_service, second_service)
        }
        transaction = self._transaction()
        primary_error = RuntimeError("primary command failure")
        real_rename = transaction_module.os.rename

        def fail_service_restores(source, destination):
            if source in {
                str(first_service) + ".blocked",
                str(second_service) + ".blocked",
            }:
                raise OSError(f"simulated restore failure: {source}")
            return real_rename(source, destination)

        with mock.patch.object(
            transaction_module.os,
            "rename",
            side_effect=fail_service_restores,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                with transaction:
                    transaction.block_files(
                        (str(first_service), str(second_service))
                    )
                    raise primary_error

        self.assertIs(captured.exception.__cause__, primary_error)
        self.assertEqual(
            [
                (failure.operation, failure.path)
                for failure in captured.exception.failures
            ],
            [
                ("restore_blocked_file", str(first_service)),
                ("restore_blocked_file", str(second_service)),
            ],
        )
        self.assertFalse(
            os.path.lexists(str(self.fs_dir / "tmp/lock_chroot"))
        )

        transaction.cleanup()
        for path, snapshot in services_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self._assert_no_transaction_residue()

    def test_cleanup_is_idempotent(self):
        service = self._seed_service("sbin/initctl")
        service_before = _node_snapshot(service)
        transaction = self._transaction()
        transaction.__enter__()
        transaction.block_files((str(service),))

        transaction.cleanup()
        first_snapshot = _node_snapshot(service)
        transaction.cleanup()

        self.assertEqual(first_snapshot, service_before)
        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_clean_reentry_succeeds_after_primary_failure(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        service_before = _node_snapshot(service)

        with self.assertRaisesRegex(
            RuntimeError,
            "first operation failed",
        ):
            with self._transaction() as first_transaction:
                first_transaction.block_files((str(service),))
                raise RuntimeError("first operation failed")

        with self._transaction():
            self.assertTrue(
                os.path.lexists(
                    str(self.fs_dir / "tmp/lock_chroot")
                )
            )

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_chroot_run_preserves_apt_helper_command_order(self):
        self._seed_managed_nodes()
        self._seed_service("sbin/initctl")
        self.ctx.apt_helper = True
        requested = ["custom-command", "--argument"]
        expected_result = SimpleNamespace(returncode=0)
        runner = _FakeChrootRunner(
            self.fs_dir,
            requested,
            requested_result=expected_result,
        )

        with self._patched_chroot(runner):
            result = chroot.chroot_run(self.ctx, *requested)

        self.assertIs(result, expected_result)
        self.assertEqual(
            runner.payloads,
            [
                ["locale-gen", self.ctx.locales],
                ["ln", "-s", "/bin/true", "/sbin/initctl"],
                ["apt-get", "update", "-qq"],
                ["dpkg", "--configure", "-a"],
                ["apt-get", "install", "-f", "-y", "-q"],
                requested,
                ["apt-get", "autoremove", "--purge"],
                ["apt-get", "autoclean"],
                ["apt-get", "clean"],
            ],
        )
        self._assert_no_transaction_residue()

    def test_chroot_run_preserves_apt_helper_disabled_behavior(self):
        self._seed_service("sbin/initctl")
        requested = ["custom-command"]
        runner = _FakeChrootRunner(self.fs_dir, requested)

        with self._patched_chroot(runner):
            chroot.chroot_run(self.ctx, *requested)

        self.assertEqual(
            runner.payloads,
            [
                ["locale-gen", self.ctx.locales],
                ["ln", "-s", "/bin/true", "/sbin/initctl"],
                requested,
                ["apt-get", "autoremove", "--purge"],
                ["apt-get", "autoclean"],
                ["apt-get", "clean"],
            ],
        )
        self._assert_no_transaction_residue()


if __name__ == "__main__":
    unittest.main()
