from __future__ import annotations

import json
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
    TransactionRecoveryError,
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
        counts = self._transaction_residual_counts()
        self.assertEqual(
            counts,
            {
                "backups": 0,
                "blocked_files": 0,
                "journals": 0,
                "locks": 0,
                "transaction_symlinks": 0,
            },
        )

    def _transaction_residual_counts(self):
        journal = (
            self.work_dir / ".liveusb-chroot-transaction.json"
        )
        pending = tuple(
            self.work_dir.glob(
                ".liveusb-chroot-transaction.json.pending-*"
            )
        )
        transaction_symlinks = 0
        for root, directory_names, file_names in os.walk(
            str(self.fs_dir),
            followlinks=False,
        ):
            for name in tuple(directory_names) + tuple(file_names):
                path = os.path.join(root, name)
                if (
                    os.path.islink(path)
                    and os.readlink(path) == "/bin/true"
                ):
                    transaction_symlinks += 1
        return {
            "backups": len(
                tuple(
                    self.fs_dir.rglob(
                        "*.liveusb-transaction-*"
                    )
                )
            ),
            "blocked_files": len(
                tuple(self.fs_dir.rglob("*.blocked"))
            ),
            "journals": int(os.path.lexists(str(journal))) + len(pending),
            "locks": int(
                os.path.lexists(
                    str(self.fs_dir / "tmp/lock_chroot")
                )
            ),
            "transaction_symlinks": transaction_symlinks,
        }

    def _simulate_process_crash(self, transaction):
        transaction._close_lock_descriptor()
        transaction._lock_owned = False

    def _tree_evidence_snapshot(self):
        evidence = {}
        for root, directory_names, file_names in os.walk(
            str(self.fs_dir),
            followlinks=False,
        ):
            for name in tuple(directory_names) + tuple(file_names):
                path = Path(root) / name
                if path.is_dir() and not path.is_symlink():
                    continue
                evidence[str(path.relative_to(self.fs_dir))] = (
                    _node_snapshot(path)
                )
        metadata_paths = (
            self.work_dir / ".liveusb-chroot-transaction.json",
            *tuple(
                self.work_dir.glob(
                    ".liveusb-chroot-transaction.json.pending-*"
                )
            ),
        )
        for path in metadata_paths:
            if os.path.lexists(str(path)):
                evidence[
                    "work/" + str(path.relative_to(self.work_dir))
                ] = _node_snapshot(path)
        return evidence

    @staticmethod
    def _remove_test_node(path):
        path = str(path)
        if not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        else:
            os.unlink(path)

    def _install_nonregular_replacement(self, path, kind):
        self._remove_test_node(path)
        if kind == "directory":
            Path(path).mkdir()
        elif kind == "symlink":
            os.symlink("/alternate-package-node", str(path))
        elif kind == "special":
            os.mkfifo(str(path), 0o600)
        else:
            raise AssertionError(
                f"Unknown replacement test kind: {kind}"
            )

    def _assert_normal_nonregular_replacement_rejected(self, kind):
        service = self._seed_service("sbin/initctl")
        transaction = self._transaction()
        transaction.__enter__()
        transaction.block_files((str(service),))
        backup_path = Path(str(service) + ".blocked")
        journal_path = Path(transaction.journal_path)
        lock_path = Path(transaction.lock_path)
        backup_before = _node_snapshot(backup_path)
        lock_before = _node_snapshot(lock_path)
        self._install_nonregular_replacement(service, kind)
        replacement_before = _node_snapshot(service)

        with self.assertRaises(TransactionCleanupError) as captured:
            transaction.cleanup()

        self.assertEqual(len(captured.exception.failures), 1)
        self.assertIsInstance(
            captured.exception.failures[0].error,
            TransactionRecoveryError,
        )
        self.assertIn(
            "not an lstat regular file",
            str(captured.exception.failures[0].error),
        )
        self.assertEqual(_node_snapshot(service), replacement_before)
        self.assertEqual(_node_snapshot(backup_path), backup_before)
        self.assertEqual(_node_snapshot(lock_path), lock_before)
        self.assertTrue(os.path.lexists(str(journal_path)))

        self._remove_test_node(service)
        self._write_file(
            service,
            b"valid replacement after operator correction\n",
            0o755,
        )
        with mock.patch.object(messages, "warning"):
            transaction.cleanup()

        self.assertEqual(
            service.read_bytes(),
            b"valid replacement after operator correction\n",
        )
        self._assert_no_transaction_residue()

    def _assert_stale_nonregular_replacement_rejected(self, kind):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        abandoned = self._transaction()
        abandoned.__enter__()
        abandoned.block_files((str(service),))
        self._install_nonregular_replacement(service, kind)
        backup_path = Path(str(service) + ".blocked")
        journal_path = Path(abandoned.journal_path)
        lock_path = Path(abandoned.lock_path)
        evidence_before = {
            "replacement": _node_snapshot(service),
            "backup": _node_snapshot(backup_path),
            "journal": _node_snapshot(journal_path),
            "lock": _node_snapshot(lock_path),
        }
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with self.assertRaisesRegex(
            TransactionRecoveryError,
            "not an lstat regular file",
        ):
            with self._transaction():
                self.fail(
                    "A nonregular replacement must fail closed"
                )

        self.assertEqual(
            {
                "replacement": _node_snapshot(service),
                "backup": _node_snapshot(backup_path),
                "journal": _node_snapshot(journal_path),
                "lock": _node_snapshot(lock_path),
            },
            evidence_before,
        )

        self._remove_test_node(service)
        self._write_file(
            service,
            b"valid replacement after stale-state review\n",
            0o755,
        )
        with mock.patch.object(messages, "warning"):
            with self._transaction():
                pass

        self.assertEqual(
            service.read_bytes(),
            b"valid replacement after stale-state review\n",
        )
        self._assert_no_transaction_residue()

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
        abandoned = self._transaction()
        abandoned._ensure_lock_directory()
        abandoned._create_new_lock()
        lock_path = Path(abandoned.lock_path)
        stale_lock = json.loads(
            lock_path.read_text(encoding="utf-8")
        )
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self._transaction():
                replacement_lock = json.loads(
                    lock_path.read_text(encoding="utf-8")
                )
                self.assertNotEqual(
                    replacement_lock["token"],
                    stale_lock["token"],
                )

        self._assert_no_transaction_residue()

    def test_forced_takeover_rejects_live_owner_without_mutation(self):
        self._seed_managed_nodes()
        abandoned = self._transaction()
        abandoned.__enter__()
        evidence_before = self._tree_evidence_snapshot()
        self.ctx.force_chroot = True

        with self.assertRaisesRegex(
            TransactionRecoveryError,
            "ownership is active",
        ):
            with self._transaction():
                self.fail("Live ownership must prevent takeover")

        self.assertEqual(
            self._tree_evidence_snapshot(),
            evidence_before,
        )
        abandoned.cleanup()
        self._assert_no_transaction_residue()

    def test_managed_recovery_records_precede_first_mutation(self):
        self._seed_managed_nodes()
        transaction = self._transaction()
        real_rename = transaction._rename_durable
        observations = []

        def inspect_first_rename(source, destination):
            if not observations:
                lock_data = json.loads(
                    Path(transaction.lock_path).read_text(
                        encoding="utf-8"
                    )
                )
                journal_data = json.loads(
                    Path(transaction.journal_path).read_text(
                        encoding="utf-8"
                    )
                )
                observations.append(
                    (
                        lock_data,
                        journal_data,
                        source,
                        destination,
                    )
                )
            return real_rename(source, destination)

        with mock.patch.object(
            transaction,
            "_rename_durable",
            side_effect=inspect_first_rename,
        ):
            with transaction:
                pass

        self.assertEqual(len(observations), 1)
        lock_data, journal_data, _source, _destination = observations[0]
        self.assertEqual(lock_data["pid"], os.getpid())
        self.assertEqual(len(lock_data["token"]), 32)
        self.assertEqual(
            journal_data["owner"],
            {
                "pid": lock_data["pid"],
                "token": lock_data["token"],
            },
        )
        self.assertEqual(
            {
                record["path"]
                for record in journal_data["managed"]
            },
            {
                "etc/hosts",
                "etc/resolv.conf",
                "etc/debian_chroot",
                "etc/mtab",
            },
        )
        self.assertEqual(len(journal_data["managed"]), 4)
        self._assert_no_transaction_residue()

    def test_service_recovery_records_precede_first_rename(self):
        first_service = self._seed_service("sbin/initctl")
        second_service = self._seed_service(
            "etc/init.d/example-service",
        )

        with self._transaction() as transaction:
            real_rename = transaction._rename_durable
            observations = []

            def inspect_first_service_rename(source, destination):
                if not observations:
                    observations.append(
                        json.loads(
                            Path(transaction.journal_path).read_text(
                                encoding="utf-8"
                            )
                        )
                    )
                return real_rename(source, destination)

            with mock.patch.object(
                transaction,
                "_rename_durable",
                side_effect=inspect_first_service_rename,
            ):
                transaction.block_files(
                    (str(first_service), str(second_service))
                )

        self.assertEqual(len(observations), 1)
        self.assertEqual(
            {
                record["path"]
                for record in observations[0]["services"]
            },
            {
                "sbin/initctl",
                "etc/init.d/example-service",
            },
        )
        self.assertEqual(len(observations[0]["services"]), 2)
        self._assert_no_transaction_residue()

    def test_crash_recovery_restores_managed_substitution_first(self):
        self._seed_managed_nodes()
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        abandoned = self._transaction()
        abandoned.__enter__()
        self.assertNotEqual(
            _node_snapshot(self.fs_dir / "etc/hosts"),
            managed_before[self.fs_dir / "etc/hosts"],
        )
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self._transaction():
                pass

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self._assert_no_transaction_residue()

    def test_crash_recovery_restores_rename_before_stub(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        service_before = _node_snapshot(service)
        abandoned = self._transaction()
        abandoned.__enter__()

        def interrupt_before_stub(_path):
            raise RuntimeError("simulated crash before stub")

        with self.assertRaisesRegex(
            RuntimeError,
            "simulated crash before stub",
        ):
            abandoned.block_files(
                (str(service),),
                interrupt_before_stub,
            )
        self.assertFalse(os.path.lexists(str(service)))
        self.assertTrue(
            os.path.lexists(str(service) + ".blocked")
        )
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self._transaction():
                pass

        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_crash_recovery_restores_completed_stub(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        service_before = _node_snapshot(service)
        abandoned = self._transaction()
        abandoned.__enter__()
        abandoned.block_files((str(service),))
        self.assertTrue(os.path.islink(str(service)))
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self._transaction():
                pass

        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_crash_recovery_preserves_package_replacement(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        abandoned = self._transaction()
        abandoned.__enter__()
        abandoned.block_files((str(service),))
        os.unlink(str(service))
        self._write_file(
            service,
            b"package replacement after crash\n",
            0o755,
        )
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ), mock.patch.object(messages, "warning"):
            with self._transaction():
                pass

        self.assertEqual(
            service.read_bytes(),
            b"package replacement after crash\n",
        )
        self._assert_no_transaction_residue()

    def test_crash_recovery_rejects_corrupt_metadata_unchanged(self):
        self._seed_managed_nodes()
        abandoned = self._transaction()
        abandoned.__enter__()
        self._simulate_process_crash(abandoned)
        journal_path = Path(abandoned.journal_path)
        journal_path.write_bytes(b"{corrupt recovery metadata")
        evidence_before = self._tree_evidence_snapshot()
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "metadata is corrupt",
            ):
                with self._transaction():
                    self.fail("Corrupt recovery metadata must fail closed")

        self.assertEqual(
            self._tree_evidence_snapshot(),
            evidence_before,
        )

    def test_crash_recovery_rejects_ambiguous_owner_unchanged(self):
        self._seed_managed_nodes()
        abandoned = self._transaction()
        abandoned.__enter__()
        self._simulate_process_crash(abandoned)
        journal_path = Path(abandoned.journal_path)
        journal_data = json.loads(
            journal_path.read_text(encoding="utf-8")
        )
        journal_data["owner"]["token"] = "f" * 32
        journal_path.write_text(
            json.dumps(
                journal_data,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        evidence_before = self._tree_evidence_snapshot()
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "ownership is invalid",
            ):
                with self._transaction():
                    self.fail("Ambiguous recovery metadata must fail closed")

        self.assertEqual(
            self._tree_evidence_snapshot(),
            evidence_before,
        )

    def test_crash_recovery_preflights_all_state_before_mutation(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        abandoned = self._transaction()
        abandoned.__enter__()
        abandoned.block_files((str(service),))
        self._simulate_process_crash(abandoned)
        hosts_backups = tuple(
            self.fs_dir.glob(
                "etc/hosts.liveusb-transaction-*"
            )
        )
        self.assertEqual(len(hosts_backups), 1)
        hosts_backups[0].write_bytes(b"ambiguous backup content\n")
        evidence_before = self._tree_evidence_snapshot()
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "backup is ambiguous",
            ):
                with self._transaction():
                    self.fail("Ambiguous state must fail before recovery")

        self.assertEqual(
            self._tree_evidence_snapshot(),
            evidence_before,
        )

    def test_crash_recovery_retries_after_interruption(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        service_before = _node_snapshot(service)
        abandoned = self._transaction()
        abandoned.__enter__()
        abandoned.block_files((str(service),))
        self._simulate_process_crash(abandoned)

        hosts_path = self.fs_dir / "etc/hosts"
        hosts_backups = tuple(
            self.fs_dir.glob(
                "etc/hosts.liveusb-transaction-*"
            )
        )
        self.assertEqual(len(hosts_backups), 1)
        hosts_backup = hosts_backups[0]
        real_rename = transaction_module.os.rename
        failed_once = False

        def interrupt_one_recovery(source, destination):
            nonlocal failed_once
            if (
                not failed_once
                and source == str(hosts_backup)
                and destination == str(hosts_path)
            ):
                failed_once = True
                raise OSError("simulated interrupted recovery")
            return real_rename(source, destination)

        self.ctx.force_chroot = True
        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ), mock.patch.object(
            transaction_module.os,
            "rename",
            side_effect=interrupt_one_recovery,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                with self._transaction():
                    self.fail("Incomplete recovery must prevent replacement")

        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "restore_managed_node",
        )
        interrupted_counts = self._transaction_residual_counts()
        self.assertEqual(interrupted_counts["locks"], 1)
        self.assertEqual(interrupted_counts["journals"], 1)
        self.assertEqual(interrupted_counts["backups"], 1)
        self.assertEqual(interrupted_counts["blocked_files"], 0)
        self.assertEqual(
            interrupted_counts["transaction_symlinks"],
            0,
        )

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            return_value=False,
        ):
            with self._transaction():
                pass

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self.assertEqual(_node_snapshot(service), service_before)
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
        self.assertTrue(
            os.path.lexists(str(self.fs_dir / "tmp/lock_chroot"))
        )
        self.assertTrue(
            os.path.lexists(
                str(
                    self.work_dir
                    / ".liveusb-chroot-transaction.json"
                )
            )
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
        self.assertTrue(
            os.path.lexists(str(self.fs_dir / "tmp/lock_chroot"))
        )
        self.assertTrue(
            os.path.lexists(
                str(
                    self.work_dir
                    / ".liveusb-chroot-transaction.json"
                )
            )
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

    def test_service_targets_are_validated_before_journal_mutation(self):
        outside_directory = self.root / "outside"
        outside_directory.mkdir()
        escape_parent = self.fs_dir / "escape-parent"
        os.symlink(str(outside_directory), str(escape_parent))
        valid_service = self._seed_service(
            "etc/init.d/valid-before-reserved"
        )
        valid_service_before = _node_snapshot(valid_service)

        with self._transaction() as transaction:
            journal_path = Path(transaction.journal_path)
            transaction_backup = self.fs_dir / (
                "sbin/example.liveusb-transaction-"
                + str(transaction._token)
            )
            cases = (
                (
                    "outside FileSystem",
                    outside_directory / "service",
                    "escapes FileSystem",
                ),
                (
                    "../ journal",
                    self.fs_dir
                    / ".."
                    / ".liveusb-chroot-transaction.json",
                    "escapes FileSystem",
                ),
                (
                    "symlink-parent escape",
                    escape_parent / "service",
                    "parent escapes FileSystem",
                ),
                *tuple(
                    (
                        f"managed node {path.name}",
                        path,
                        "reserved service target",
                    )
                    for path in self._managed_paths()
                ),
                (
                    "lock path",
                    Path(transaction.lock_path),
                    "reserved service target",
                ),
                (
                    ".blocked path",
                    self.fs_dir / "sbin/example.blocked",
                    "reserved service target",
                ),
                (
                    "transaction backup path",
                    transaction_backup,
                    "reserved service target",
                ),
            )

            for label, target, expected_error in cases:
                with self.subTest(label=label):
                    journal_before = journal_path.read_bytes()
                    with self.assertRaisesRegex(
                        TransactionRecoveryError,
                        expected_error,
                    ):
                        transaction.block_files((str(target),))
                    self.assertEqual(
                        journal_path.read_bytes(),
                        journal_before,
                    )

            journal_before = journal_path.read_bytes()
            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "reserved service target",
            ):
                transaction.block_files(
                    (
                        str(valid_service),
                        str(self.fs_dir / "etc/hosts"),
                    )
                )
            self.assertEqual(
                journal_path.read_bytes(),
                journal_before,
            )
            self.assertEqual(
                _node_snapshot(valid_service),
                valid_service_before,
            )
            self.assertFalse(
                os.path.lexists(str(valid_service) + ".blocked")
            )

        self._assert_no_transaction_residue()

    def test_orphan_recovery_artifacts_are_rejected_without_lock(self):
        final_journal = (
            self.work_dir / ".liveusb-chroot-transaction.json"
        )
        pending_journal = self.work_dir / (
            ".liveusb-chroot-transaction.json.pending-orphan"
        )
        blocked_path = self.fs_dir / "sbin/orphan.blocked"
        transaction_backup = self.fs_dir / (
            "etc/hosts.liveusb-transaction-" + ("a" * 32)
        )
        cases = (
            ("final journal", final_journal, b"final evidence\n"),
            ("pending journal", pending_journal, b"pending evidence\n"),
            (".blocked file", blocked_path, b"blocked evidence\n"),
            (
                "transaction backup",
                transaction_backup,
                b"backup evidence\n",
            ),
        )

        for label, path, content in cases:
            with self.subTest(label=label):
                self._write_file(path, content, 0o600)
                evidence_before = self._tree_evidence_snapshot()
                with self.assertRaises(TransactionRecoveryError):
                    with self._transaction():
                        self.fail(
                            "Orphan recovery evidence must fail closed"
                        )
                self.assertEqual(
                    self._tree_evidence_snapshot(),
                    evidence_before,
                )
                self.assertFalse(
                    os.path.lexists(
                        str(self.fs_dir / "tmp/lock_chroot")
                    )
                )
                os.unlink(str(path))

        self._assert_no_transaction_residue()

    def test_stale_blocked_file_observed_after_entry_fails_closed(self):
        service = self._seed_service("sbin/initctl")
        service_before = _node_snapshot(service)

        with self._transaction() as transaction:
            stale_backup = Path(str(service) + ".blocked")
            self._write_file(
                stale_backup,
                b"stale blocked evidence\n",
                0o600,
            )
            journal_path = Path(transaction.journal_path)
            journal_before = journal_path.read_bytes()

            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "Stale blocked-file backup",
            ):
                transaction.block_files((str(service),))

            self.assertEqual(
                journal_path.read_bytes(),
                journal_before,
            )
            self.assertEqual(
                stale_backup.read_bytes(),
                b"stale blocked evidence\n",
            )
            os.unlink(str(stale_backup))

        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    def test_pending_only_evidence_survives_unexpected_residue(self):
        abandoned = self._transaction()
        abandoned._ensure_lock_directory()
        abandoned._create_new_lock()
        lock_data = json.loads(
            Path(abandoned.lock_path).read_text(encoding="utf-8")
        )
        pending_path = Path(
            abandoned.journal_pending_prefix + "preserve-evidence"
        )
        pending_data = {
            "managed": [],
            "owner": {
                "pid": lock_data["pid"],
                "token": lock_data["token"],
            },
            "sequence": 1,
            "services": [],
            "version": 1,
        }
        pending_path.write_bytes(
            abandoned._encode_json(pending_data)
        )
        residue_path = self.fs_dir / "sbin/unexpected.blocked"
        self._write_file(
            residue_path,
            b"unexpected residue evidence\n",
            0o600,
        )
        self._simulate_process_crash(abandoned)
        evidence_before = self._tree_evidence_snapshot()
        self.ctx.force_chroot = True

        with self.assertRaisesRegex(
            TransactionRecoveryError,
            "Unjournaled transaction residue",
        ):
            with self._transaction():
                self.fail(
                    "Pending evidence with residue must fail closed"
                )

        self.assertEqual(
            self._tree_evidence_snapshot(),
            evidence_before,
        )
        self.assertEqual(
            pending_path.read_bytes(),
            abandoned._encode_json(pending_data),
        )
        self.assertEqual(
            residue_path.read_bytes(),
            b"unexpected residue evidence\n",
        )

    def test_unlocked_current_pid_metadata_does_not_veto_recovery(self):
        abandoned = self._transaction()
        abandoned._ensure_lock_directory()
        abandoned._create_new_lock()
        lock_path = Path(abandoned.lock_path)
        lock_data = json.loads(
            lock_path.read_text(encoding="utf-8")
        )
        self.assertEqual(lock_data["pid"], os.getpid())
        self._simulate_process_crash(abandoned)
        self.ctx.force_chroot = True

        with mock.patch.object(
            ChrootTransaction,
            "_pid_is_live",
            side_effect=AssertionError(
                "PID inspection must not veto an acquired OS lock"
            ),
        ):
            with self._transaction():
                pass

        self._assert_no_transaction_residue()

    def test_normal_cleanup_rejects_directory_replacement(self):
        self._assert_normal_nonregular_replacement_rejected(
            "directory"
        )

    def test_normal_cleanup_rejects_alternate_symlink_replacement(self):
        self._assert_normal_nonregular_replacement_rejected(
            "symlink"
        )

    @unittest.skipUnless(
        hasattr(os, "mkfifo"),
        "FIFO nodes require POSIX os.mkfifo",
    )
    def test_normal_cleanup_rejects_special_node_replacement(self):
        self._assert_normal_nonregular_replacement_rejected(
            "special"
        )

    def test_stale_recovery_rejects_directory_replacement(self):
        self._assert_stale_nonregular_replacement_rejected(
            "directory"
        )

    def test_stale_recovery_rejects_alternate_symlink_replacement(self):
        self._assert_stale_nonregular_replacement_rejected(
            "symlink"
        )

    @unittest.skipUnless(
        hasattr(os, "mkfifo"),
        "FIFO nodes require POSIX os.mkfifo",
    )
    def test_stale_recovery_rejects_special_node_replacement(self):
        self._assert_stale_nonregular_replacement_rejected(
            "special"
        )

    def test_journal_removal_failure_is_retryable(self):
        transaction = self._transaction()
        transaction.__enter__()
        journal_path = Path(transaction.journal_path)
        lock_path = Path(transaction.lock_path)
        real_unlink = transaction_module.os.unlink
        failed_once = False

        def fail_journal_removal_once(path):
            nonlocal failed_once
            if not failed_once and path == str(journal_path):
                failed_once = True
                raise OSError("simulated journal removal failure")
            return real_unlink(path)

        with mock.patch.object(
            transaction_module.os,
            "unlink",
            side_effect=fail_journal_removal_once,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                transaction.cleanup()

        self.assertTrue(failed_once)
        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "remove_transaction_journal",
        )
        self.assertTrue(os.path.lexists(str(journal_path)))
        self.assertTrue(os.path.lexists(str(lock_path)))

        transaction.cleanup()
        self._assert_no_transaction_residue()

    def test_lock_identity_validation_failure_is_retryable(self):
        transaction = self._transaction()
        transaction.__enter__()
        journal_path = Path(transaction.journal_path)
        lock_path = Path(transaction.lock_path)
        lock_before = _node_snapshot(lock_path)
        real_lstat = transaction_module.os.lstat
        failed_once = False
        inactive_lock_checks = 0

        def report_one_false_lock_identity(path):
            nonlocal failed_once, inactive_lock_checks
            stat_result = real_lstat(path)
            if (
                not failed_once
                and str(path) == str(lock_path)
                and not transaction._journal_active
            ):
                inactive_lock_checks += 1
                if inactive_lock_checks == 2:
                    failed_once = True
                    return SimpleNamespace(
                        st_dev=stat_result.st_dev,
                        st_ino=stat_result.st_ino + 1,
                        st_mode=stat_result.st_mode,
                    )
            return stat_result

        with mock.patch.object(
            transaction_module.os,
            "lstat",
            side_effect=report_one_false_lock_identity,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                transaction.cleanup()

        self.assertTrue(failed_once)
        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "release_lock",
        )
        self.assertFalse(os.path.lexists(str(journal_path)))
        self.assertEqual(_node_snapshot(lock_path), lock_before)

        transaction.cleanup()
        self._assert_no_transaction_residue()

    def test_created_lock_directory_cleanup_failure_is_retryable(self):
        lock_directory = self.fs_dir / "tmp"
        os.rmdir(str(lock_directory))
        transaction = self._transaction()
        transaction.__enter__()
        real_rmdir = transaction_module.os.rmdir
        failed_once = False

        def fail_lock_directory_removal_once(path):
            nonlocal failed_once
            if (
                not failed_once
                and str(path) == str(lock_directory)
            ):
                failed_once = True
                raise OSError(
                    "simulated lock-directory removal failure"
                )
            return real_rmdir(path)

        with mock.patch.object(
            transaction_module.os,
            "rmdir",
            side_effect=fail_lock_directory_removal_once,
        ):
            with self.assertRaises(TransactionCleanupError) as captured:
                transaction.cleanup()

        self.assertTrue(failed_once)
        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "remove_lock_directory",
        )
        self.assertTrue(lock_directory.is_dir())
        self.assertEqual(tuple(lock_directory.iterdir()), tuple())

        transaction.cleanup()
        self.assertFalse(os.path.lexists(str(lock_directory)))
        self._assert_no_transaction_residue()

    @unittest.skipUnless(
        hasattr(os, "fork"),
        "Actual crash recovery requires POSIX os.fork",
    )
    def test_child_process_os_exit_is_recovered_without_residue(self):
        self._seed_managed_nodes()
        service = self._seed_service("sbin/initctl")
        managed_before = {
            path: _node_snapshot(path)
            for path in self._managed_paths()
        }
        service_before = _node_snapshot(service)

        child_pid = os.fork()
        if child_pid == 0:
            try:
                transaction = self._transaction()
                transaction.__enter__()
                transaction.block_files((str(service),))
            except BaseException:
                os._exit(2)
            os._exit(0)

        _waited_pid, status = os.waitpid(child_pid, 0)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)
        interrupted_counts = self._transaction_residual_counts()
        self.assertEqual(interrupted_counts["locks"], 1)
        self.assertEqual(interrupted_counts["journals"], 1)
        self.ctx.force_chroot = True

        with self._transaction():
            pass

        for path, snapshot in managed_before.items():
            self.assertEqual(_node_snapshot(path), snapshot)
        self.assertEqual(_node_snapshot(service), service_before)
        self._assert_no_transaction_residue()

    @unittest.skipUnless(
        hasattr(os, "fork"),
        "Live OS-lock proof requires POSIX os.fork",
    )
    def test_live_child_os_lock_holder_is_rejected_by_flock(self):
        self._seed_managed_nodes()
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        child_pid = os.fork()

        if child_pid == 0:
            os.close(ready_read)
            os.close(release_write)
            exit_code = 0
            try:
                transaction = self._transaction()
                transaction.__enter__()
                os.write(ready_write, b"1")
                os.read(release_read, 1)
                transaction.cleanup()
            except BaseException:
                exit_code = 2
                try:
                    os.write(ready_write, b"0")
                except OSError:
                    pass
            finally:
                os.close(ready_write)
                os.close(release_read)
            os._exit(exit_code)

        os.close(ready_write)
        os.close(release_read)
        ready = os.read(ready_read, 1)
        os.close(ready_read)
        child_status = None
        try:
            self.assertEqual(ready, b"1")
            evidence_before = self._tree_evidence_snapshot()
            self.ctx.force_chroot = True
            with self.assertRaisesRegex(
                TransactionRecoveryError,
                "ownership is active",
            ):
                with self._transaction():
                    self.fail(
                        "A live OS-lock holder must prevent takeover"
                    )
            self.assertEqual(
                self._tree_evidence_snapshot(),
                evidence_before,
            )
        finally:
            try:
                os.write(release_write, b"1")
            except BrokenPipeError:
                pass
            os.close(release_write)
            _waited_pid, child_status = os.waitpid(child_pid, 0)

        self.assertIsNotNone(child_status)
        self.assertTrue(os.WIFEXITED(child_status))
        self.assertEqual(os.WEXITSTATUS(child_status), 0)
        self._assert_no_transaction_residue()


if __name__ == "__main__":
    unittest.main()
