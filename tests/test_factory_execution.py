from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import factory_execution
from liveusb.backend import factory_plan
from liveusb.backend import mount_session
from liveusb.backend import preflight
from liveusb.backend import preflight_runtime
from liveusb.backend import transaction


VERSION_OUTPUTS = {
    "mksquashfs": "mksquashfs version 4.6.1\n",
    "unsquashfs": "unsquashfs version 4.6.1\n",
    "rsync": "rsync  version 3.2.7  protocol version 31\n",
    "genisoimage": "genisoimage 1.1.11 (Linux)\n",
    "isohybrid": "isohybrid version 0.12\n",
    "chroot": "chroot (GNU coreutils) 9.4\n",
    "mount": "mount from util-linux 2.39.3\n",
    "umount": "umount from util-linux 2.39.3\n",
    "xorriso": "xorriso 1.5.6 : RockRidge filesystem manipulator\n",
    "qemu-system-x86_64": "QEMU emulator version 8.2.2\n",
}

ISOINFO_LEGACY_LISTING = """\
Directory listing of /
d---------   0    0    0            2048 Aug 04 2026 [     20 02]  .disk
d---------   0    0    0            2048 Aug 04 2026 [     21 02]  casper
d---------   0    0    0            2048 Aug 04 2026 [     22 02]  isolinux
Directory listing of /isolinux/
----------   0    0    0              16 Aug 04 2026 [     23 00]  isolinux.bin
"""


class _CompletedExecutor:
    def __init__(self, _ctx, _authorization, **_options):
        self.records = (
            {
                "argv": ["${TOOL:chroot}", "${FILESYSTEM_ROOT}"],
                "authority": "exact",
                "error_type": None,
                "returncode": 0,
                "stage": "synthetic-complete",
            },
        )
        self.assertions = 0

    def assert_complete(self):
        self.assertions += 1


class FactoryExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.mount_root = self.root / "mount"
        self.mount_root.mkdir(mode=0o700)
        self.runtime_parent = self.root / "run"
        self.runtime_parent.mkdir(mode=0o700)
        self.source = self.root / "source.iso"
        self.source.write_bytes(b"accepted synthetic legacy ISO")
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(mode=0o700)
        for name in preflight_runtime.VERSION_TOOL_ORDER:
            self.add_executable(name)
        self.add_executable("isoinfo")
        self.exclude_file = self.root / "exclude"
        self.exclude_file.write_text("tmp/*\n", encoding="ascii")
        self.records = self.root / "records"
        self.records.mkdir(mode=0o700)
        self.ctx = Context(
            work_dir=str(self.work),
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.runtime_parent / "liveusb"),
            iso=str(self.source),
        )
        self.make_legacy_tree()

    def add_executable(self, name):
        path = self.bin_dir / name
        path.write_bytes(b"synthetic executable\n")
        path.chmod(0o700)
        return path

    def resolver(self, name):
        path = self.bin_dir / name
        return str(path) if path.exists() else None

    @staticmethod
    def capacity(available_bytes=128 * 1024 ** 3):
        block_size = 4096
        blocks = available_bytes // block_size
        return types.SimpleNamespace(
            f_bavail=blocks,
            f_bfree=blocks,
            f_blocks=blocks * 2,
            f_bsize=block_size,
            f_frsize=block_size,
        )

    def preflight_engine(self):
        def missing_kvm(_path):
            raise FileNotFoundError("Synthetic KVM node is absent")

        return preflight.PreflightEngine(
            which=self.resolver,
            statvfs=lambda _path: self.capacity(),
            mountinfo_reader=lambda: tuple(),
            lock_text_reader=lambda: "",
            effective_uid=os.geteuid,
            expected_owner_uid=os.geteuid(),
            cpu_count_reader=lambda: 4,
            loadavg_reader=lambda: (1.0, 1.0, 1.0),
            meminfo_reader=lambda: (
                "MemTotal: 32000000 kB\n"
                "MemAvailable: 24000000 kB\n"
                "SwapTotal: 8000000 kB\n"
                "SwapFree: 8000000 kB\n"
            ),
            machine_reader=lambda: "x86_64",
            kvm_state_reader=missing_kvm,
        )

    def runtime_executor(self, command, **_options):
        name = os.path.basename(command[0])
        if name == "isoinfo":
            return preflight_runtime.CommandOutcome(
                0,
                stdout=ISOINFO_LEGACY_LISTING.encode("ascii"),
                termination_confirmed=True,
            )
        return preflight_runtime.CommandOutcome(
            1 if name == "unsquashfs" else 0,
            stdout=VERSION_OUTPUTS[name].encode("ascii"),
            termination_confirmed=True,
        )

    def make_legacy_tree(self):
        for relative in (
            "FileSystem/etc",
            "FileSystem/usr",
            "FileSystem/root",
            "FileSystem/tmp",
            "FileSystem/var/lib/dpkg",
            "FileSystem/boot",
            "ISO/isolinux",
            "ISO/casper",
            "ISO/.disk",
        ):
            (self.work / relative).mkdir(parents=True, exist_ok=True)
        (self.work / "ISO/isolinux/isolinux.bin").write_bytes(
            b"legacy boot image"
        )
        (self.work / "FileSystem/etc/lsb-release").write_text(
            "DISTRIB_ID=ubuntuDE\n"
            "DISTRIB_RELEASE=14.04\n"
            "DISTRIB_CODENAME=trusty\n",
            encoding="ascii",
        )
        (self.work / "FileSystem/etc/casper.conf").write_text(
            "export USERNAME=ubuntu\n",
            encoding="ascii",
        )
        (self.work / "FileSystem/var/lib/dpkg/arch").write_text(
            "amd64\n",
            encoding="ascii",
        )
        (self.work / "FileSystem/boot/initrd.img-legacy").write_bytes(
            b"legacy initrd"
        )
        (self.work / "FileSystem/boot/vmlinuz-legacy").write_bytes(
            b"legacy kernel"
        )

    def engine(self, tokens=None):
        selected_tokens = iter(
            tokens
            or (
                "1" * 32,
                "2" * 32,
            )
        )
        preflight_engine = self.preflight_engine()
        runtime_engine = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=self.runtime_executor,
        )
        planner = factory_plan.FactoryPlanEngine(
            statvfs=lambda _path: self.capacity(),
            preflight_engine=preflight_engine,
            expected_tool_owner_uid=os.geteuid(),
            exclude_file=str(self.exclude_file),
        )
        return factory_execution.FactoryExecutionEngine(
            preflight_engine=preflight_engine,
            runtime_engine=runtime_engine,
            plan_engine=planner,
            compression_probe=lambda _compression, scratch_root=None: True,
            token_factory=lambda: uuid.UUID(hex=next(selected_tokens)),
            expected_tool_owner_uid=os.geteuid(),
        )

    def issue(self):
        return factory_execution.issue_complete_rebuild(
            self.ctx,
            str(self.records),
            engine=self.engine(),
        )

    def test_complete_rebuild_plan_binds_static_and_dynamic_authorities(self):
        authorization = self.engine().plan_complete_rebuild(self.ctx)

        self.assertTrue(authorization.factory_authority_granted)
        self.assertEqual(authorization.kernel["mode"], "update-initramfs")
        stages = tuple(
            item["stage"]
            for item in authorization.exact_commands
        )
        self.assertEqual(
            stages[:5],
            (
                "target-architecture-observation",
                "system-mount-1",
                "system-mount-2",
                "system-mount-3",
                "kernel-target-1",
            ),
        )
        self.assertIn("squashfs-build", stages)
        self.assertIn("identity-derived-unmounts", stages)
        self.assertIn("service-block-and-cleanup", stages)
        self.assertEqual(len(authorization.mutation_authority), 3)
        encoded = authorization.receipt.to_json()
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("/proc/self/fd/", encoded)
        self.assertIn("${FILESYSTEM_ROOT}", encoded)

    def test_missing_kernel_selects_only_two_install_targets(self):
        (self.work / "FileSystem/boot/initrd.img-legacy").unlink()
        (self.work / "FileSystem/boot/vmlinuz-legacy").unlink()

        authorization = self.engine().plan_complete_rebuild(self.ctx)
        kernel_targets = tuple(
            item
            for item in authorization.exact_commands
            if item["stage"].startswith("kernel-target-")
        )

        self.assertTrue(authorization.factory_authority_granted)
        self.assertEqual(authorization.kernel["mode"], "install-kernel")
        self.assertEqual(len(kernel_targets), 2)
        self.assertEqual(kernel_targets[0]["argv"][-6:-4], ("apt-get", "purge"))
        self.assertEqual(kernel_targets[1]["argv"][-6:-4], ("apt-get", "install"))

    def test_target_metadata_failure_refuses_all_authority(self):
        (self.work / "FileSystem/var/lib/dpkg/arch").unlink()

        authorization = self.engine().plan_complete_rebuild(self.ctx)

        self.assertFalse(authorization.factory_authority_granted)
        self.assertIsNone(authorization.grant_id)
        self.assertTrue(
            any(
                reason.startswith("target-metadata:")
                for reason in authorization.reasons
            )
        )

    def test_issued_bundle_is_private_complete_and_unconsumed(self):
        authorization, bundle, receipt = self.issue()
        bundle_path = Path(bundle)

        self.assertTrue(authorization.factory_authority_granted)
        self.assertIs(receipt, authorization.receipt)
        self.assertEqual(stat.S_IMODE(bundle_path.stat().st_mode), 0o700)
        grant_path = bundle_path / "grant.json"
        state_path = bundle_path / "state.json"
        self.assertEqual(stat.S_IMODE(grant_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
        state = json.loads(state_path.read_text(encoding="ascii"))
        self.assertEqual(state["phase"], factory_execution.STATE_ISSUED)
        grant_text = grant_path.read_text(encoding="ascii")
        self.assertNotIn(str(self.root), grant_text)
        self.assertNotIn("stdout", grant_text)
        self.assertFalse((bundle_path / "outcome.json").exists())

    def test_symlinked_bundle_is_rejected_before_state_mutation(self):
        _authorization, bundle, _receipt = self.issue()
        bundle_path = Path(bundle)
        state_before = (bundle_path / "state.json").read_bytes()
        real_bundle = bundle_path.with_name(bundle_path.name + "-real")
        bundle_path.rename(real_bundle)
        bundle_path.symlink_to(real_bundle.name)
        rebuild_runner = mock.Mock()

        with self.assertRaises(factory_execution.FactoryExecutionError):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                str(bundle_path),
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )

        rebuild_runner.assert_not_called()
        self.assertEqual(
            (real_bundle / "state.json").read_bytes(),
            state_before,
        )
        self.assertFalse((real_bundle / "outcome.json").exists())

    def test_hard_linked_state_is_rejected_before_state_mutation(self):
        _authorization, bundle, _receipt = self.issue()
        bundle_path = Path(bundle)
        state_path = bundle_path / "state.json"
        state_before = state_path.read_bytes()
        state_alias = bundle_path / "state-alias.json"
        os.link(state_path, state_alias)
        rebuild_runner = mock.Mock()

        with self.assertRaisesRegex(ValueError, "custody"):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )

        rebuild_runner.assert_not_called()
        self.assertEqual(state_path.read_bytes(), state_before)
        self.assertEqual(state_alias.read_bytes(), state_before)
        self.assertEqual(os.lstat(state_path).st_nlink, 2)
        self.assertFalse((bundle_path / "outcome.json").exists())

    def test_symlinked_record_lock_is_rejected_before_state_mutation(self):
        _authorization, bundle, _receipt = self.issue()
        bundle_path = Path(bundle)
        state_before = (bundle_path / "state.json").read_bytes()
        lock_path = self.records / "factory-execution.lock"
        lock_path.unlink()
        foreign_lock = self.root / "foreign-lock"
        foreign_lock.write_bytes(b"foreign lock evidence")
        foreign_lock.chmod(0o600)
        lock_path.symlink_to(foreign_lock)
        rebuild_runner = mock.Mock()

        with self.assertRaises(OSError):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )

        rebuild_runner.assert_not_called()
        self.assertTrue(lock_path.is_symlink())
        self.assertEqual(foreign_lock.read_bytes(), b"foreign lock evidence")
        self.assertEqual(
            (bundle_path / "state.json").read_bytes(),
            state_before,
        )
        self.assertFalse((bundle_path / "outcome.json").exists())

    def test_fresh_evidence_drift_revokes_without_execution(self):
        _authorization, bundle, _receipt = self.issue()
        self.source.write_bytes(b"changed source bytes")
        rebuild_runner = mock.Mock()

        fresh, returned_bundle, receipt = (
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )
        )

        self.assertEqual(returned_bundle, bundle)
        self.assertTrue(fresh.factory_authority_granted)
        self.assertEqual(
            receipt.payload["status"],
            factory_execution.STATE_REVOKED,
        )
        rebuild_runner.assert_not_called()
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_REVOKED)

    def test_changed_workspace_revokes_without_execution(self):
        _authorization, bundle, _receipt = self.issue()
        relocated_work = self.root / "relocated-work"
        shutil.copytree(self.work, relocated_work)
        relocated_ctx = Context(
            work_dir=str(relocated_work),
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.runtime_parent / "liveusb"),
            iso=str(self.source),
        )
        rebuild_runner = mock.Mock()

        fresh, returned_bundle, receipt = (
            factory_execution.execute_issued_rebuild(
                relocated_ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )
        )

        self.assertEqual(returned_bundle, bundle)
        self.assertTrue(fresh.factory_authority_granted)
        self.assertEqual(
            receipt.payload["status"],
            factory_execution.STATE_REVOKED,
        )
        self.assertEqual(receipt.payload["commands_executed"], 0)
        rebuild_runner.assert_not_called()
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_REVOKED)

    def test_one_issued_grant_executes_once_and_persists_outcome(self):
        _authorization, bundle, _receipt = self.issue()
        rebuild_runner = mock.Mock()

        with mock.patch.object(
            factory_execution,
            "RebuildCommandExecutor",
            _CompletedExecutor,
        ):
            authorization, returned_bundle, receipt = (
                factory_execution.execute_issued_rebuild(
                    self.ctx,
                    bundle,
                    engine=self.engine(),
                    rebuild_runner=rebuild_runner,
                )
            )

        self.assertTrue(authorization.factory_authority_granted)
        self.assertEqual(returned_bundle, bundle)
        self.assertEqual(
            receipt.payload["status"],
            factory_execution.STATE_SUCCEEDED,
        )
        rebuild_runner.assert_called_once()
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_SUCCEEDED)
        self.assertTrue((Path(bundle) / "outcome.json").exists())

        with self.assertRaises(factory_execution.FactoryExecutionError):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=rebuild_runner,
            )
        rebuild_runner.assert_called_once()

    def test_outcome_failure_chains_the_factory_failure(self):
        _authorization, bundle, _receipt = self.issue()
        factory_error = RuntimeError("synthetic factory failure")
        outcome_error = OSError("synthetic outcome persistence failure")

        with mock.patch.object(
            factory_execution,
            "RebuildCommandExecutor",
            _CompletedExecutor,
        ), mock.patch.object(
            factory_execution.FactoryRecordStore,
            "finalize",
            side_effect=outcome_error,
        ):
            with self.assertRaises(OSError) as caught:
                factory_execution.execute_issued_rebuild(
                    self.ctx,
                    bundle,
                    engine=self.engine(),
                    rebuild_runner=mock.Mock(side_effect=factory_error),
                )

        self.assertIs(caught.exception, outcome_error)
        self.assertIs(caught.exception.__cause__, factory_error)
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_CONSUMED)

    def test_consumed_grant_recovery_runs_no_factory_command(self):
        authorization, bundle, _receipt = self.issue()
        owner_uid = os.lstat(self.records).st_uid
        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            store.consume(bundle, authorization.grant_id)
        recovery_runner = mock.Mock(return_value=False)

        returned_bundle, receipt = factory_execution.recover_consumed_rebuild(
            self.ctx,
            bundle,
            recovery_runner=recovery_runner,
        )

        self.assertEqual(returned_bundle, bundle)
        recovery_runner.assert_called_once()
        self.assertEqual(receipt.payload["replayed_factory_commands"], 0)
        self.assertEqual(
            receipt.payload["status"],
            factory_execution.STATE_INTERRUPTED,
        )
        self.assertFalse(
            receipt.payload["original_command_outcomes_available"]
        )
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_INTERRUPTED)

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX fork is required")
    def test_child_death_after_consumption_never_reissues_the_grant(self):
        authorization, bundle, _receipt = self.issue()
        child = os.fork()
        if child == 0:
            owner_uid = os.lstat(self.records).st_uid
            with factory_execution.FactoryRecordStore(
                str(self.records),
                expected_owner_uid=owner_uid,
            ) as store:
                store.consume(bundle, authorization.grant_id)
            os._exit(0)
        _pid, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_CONSUMED)

        factory_calls = mock.Mock(return_value=False)
        _bundle, receipt = factory_execution.recover_consumed_rebuild(
            self.ctx,
            bundle,
            recovery_runner=factory_calls,
        )

        factory_calls.assert_called_once()
        self.assertEqual(receipt.payload["replayed_factory_commands"], 0)
        with self.assertRaises(factory_execution.FactoryExecutionError):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
            )

    def test_durable_outcome_reconciles_without_repeating_recovery(self):
        authorization, bundle, _receipt = self.issue()
        owner_uid = os.lstat(self.records).st_uid
        terminal_receipt = factory_execution._outcome_receipt(
            authorization,
            factory_execution.STATE_FAILED,
            (),
            {
                "blocked_files": 0,
                "chroot_journal_present": False,
                "mount_journal_present": False,
            },
        )
        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            store.consume(bundle, authorization.grant_id)
            with mock.patch.object(
                store,
                "_replace_state",
                side_effect=OSError("state persistence interrupted"),
            ):
                with self.assertRaises(OSError):
                    store.finalize(
                        bundle,
                        factory_execution.STATE_FAILED,
                        terminal_receipt,
                    )

        recovery_runner = mock.Mock()
        _bundle, reconciled = factory_execution.recover_consumed_rebuild(
            self.ctx,
            bundle,
            recovery_runner=recovery_runner,
        )

        recovery_runner.assert_not_called()
        self.assertEqual(
            reconciled.payload["status"],
            factory_execution.STATE_FAILED,
        )
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_FAILED)

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX fork is required")
    def test_recovery_restores_a_stale_chroot_transaction(self):
        authorization, bundle, _receipt = self.issue()
        owner_uid = os.lstat(self.records).st_uid
        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            store.consume(bundle, authorization.grant_id)

        child = os.fork()
        if child == 0:
            stale = transaction.ChrootTransaction(self.ctx)
            stale.__enter__()
            os._exit(0)
        _pid, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        journal = self.work / ".liveusb-chroot-transaction.json"
        self.assertTrue(journal.exists())

        returned_bundle, receipt = (
            factory_execution.recover_consumed_rebuild(
                self.ctx,
                bundle,
                runner=mock.Mock(
                    return_value=types.SimpleNamespace(returncode=0)
                ),
                mountinfo_reader=lambda: tuple(),
            )
        )

        self.assertEqual(returned_bundle, bundle)
        self.assertTrue(
            receipt.payload["recovered_chroot_transaction"]
        )
        self.assertFalse(receipt.payload["recovered_publication"])
        self.assertFalse(journal.exists())
        self.assertFalse(
            (self.work / "FileSystem/tmp/lock_chroot").exists()
        )

    def test_durable_revocation_reconciles_without_replanning(self):
        authorization, bundle, _receipt = self.issue()
        owner_uid = os.lstat(self.records).st_uid
        grant = authorization.grant_payload()
        revoked_receipt = factory_execution._revocation_receipt(
            grant,
            authorization,
        )
        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            with mock.patch.object(
                store,
                "_replace_state",
                side_effect=OSError("state persistence interrupted"),
            ):
                with self.assertRaises(OSError):
                    store.revoke(bundle, revoked_receipt)

        recovery_runner = mock.Mock()
        returned_bundle, reconciled = (
            factory_execution.recover_consumed_rebuild(
                self.ctx,
                bundle,
                recovery_runner=recovery_runner,
            )
        )

        self.assertEqual(returned_bundle, bundle)
        recovery_runner.assert_not_called()
        self.assertEqual(
            reconciled.payload["status"],
            factory_execution.STATE_REVOKED,
        )
        state = json.loads(
            (Path(bundle) / "state.json").read_text(encoding="ascii")
        )
        self.assertEqual(state["phase"], factory_execution.STATE_REVOKED)

    def test_terminal_outcome_mismatch_fails_closed(self):
        _authorization, bundle, _receipt = self.issue()
        with mock.patch.object(
            factory_execution,
            "RebuildCommandExecutor",
            _CompletedExecutor,
        ):
            factory_execution.execute_issued_rebuild(
                self.ctx,
                bundle,
                engine=self.engine(),
                rebuild_runner=mock.Mock(),
            )
        outcome_path = Path(bundle) / "outcome.json"
        outcome = json.loads(outcome_path.read_text(encoding="ascii"))
        outcome["status"] = factory_execution.STATE_FAILED
        outcome_path.write_text(
            json.dumps(outcome, sort_keys=True) + "\n",
            encoding="ascii",
        )

        with self.assertRaises(factory_execution.FactoryExecutionError):
            factory_execution.recover_consumed_rebuild(
                self.ctx,
                bundle,
            )

    def test_state_sequence_mismatch_fails_closed(self):
        _authorization, bundle, _receipt = self.issue()
        state_path = Path(bundle) / "state.json"
        state = json.loads(state_path.read_text(encoding="ascii"))
        state["sequence"] = 9
        state_path.write_text(
            json.dumps(state, sort_keys=True) + "\n",
            encoding="ascii",
        )
        owner_uid = os.lstat(self.records).st_uid

        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            with self.assertRaises(factory_execution.FactoryExecutionError):
                store.read_state(bundle)

    def test_pending_state_transition_reconciles_after_replace_failure(self):
        authorization, bundle, _receipt = self.issue()
        owner_uid = os.lstat(self.records).st_uid
        real_replace = os.replace

        with factory_execution.FactoryRecordStore(
            str(self.records),
            expected_owner_uid=owner_uid,
        ) as store:
            with mock.patch.object(
                factory_execution.os,
                "replace",
                side_effect=OSError("state replacement interrupted"),
            ):
                with self.assertRaises(OSError):
                    store.consume(bundle, authorization.grant_id)

        pending = tuple(Path(bundle).glob(".state.pending-*"))
        self.assertEqual(len(pending), 1)
        with mock.patch.object(
            factory_execution.os,
            "replace",
            wraps=real_replace,
        ) as replace:
            with factory_execution.FactoryRecordStore(
                str(self.records),
                expected_owner_uid=owner_uid,
            ) as store:
                state = store.read_state(bundle)

        self.assertEqual(state["phase"], factory_execution.STATE_CONSUMED)
        replace.assert_called_once()
        self.assertEqual(tuple(Path(bundle).glob(".state.pending-*")), ())

    def test_record_lock_rejects_a_competing_holder(self):
        first = factory_execution.FactoryRecordStore(str(self.records))
        second = factory_execution.FactoryRecordStore(str(self.records))
        with first:
            with self.assertRaises(BlockingIOError):
                second.__enter__()

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX fork is required")
    def test_record_lock_rejects_a_forked_competing_holder(self):
        read_descriptor, write_descriptor = os.pipe()
        with factory_execution.FactoryRecordStore(str(self.records)):
            child = os.fork()
            if child == 0:
                os.close(read_descriptor)
                result = b"unexpected"
                try:
                    with factory_execution.FactoryRecordStore(
                        str(self.records)
                    ):
                        pass
                except BlockingIOError:
                    result = b"blocked"
                except BaseException:
                    result = b"error"
                os.write(write_descriptor, result)
                os.close(write_descriptor)
                os._exit(0)
            os.close(write_descriptor)
            result = os.read(read_descriptor, 32)
            os.close(read_descriptor)
            _pid, status = os.waitpid(child, 0)

        self.assertEqual(status, 0)
        self.assertEqual(result, b"blocked")

    def test_factory_child_lease_holds_lock_after_parent_release(self):
        process = None
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        try:
            with factory_execution.FactoryRecordStore(
                str(self.records)
            ) as store:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os,sys;"
                            "os.write(int(sys.argv[1]),b'ready');"
                            "os.read(int(sys.argv[2]),1)"
                        ),
                        str(ready_write),
                        str(release_read),
                    ],
                    close_fds=True,
                    pass_fds=(
                        store.lease_descriptor,
                        ready_write,
                        release_read,
                    ),
                )
                os.close(ready_write)
                ready_write = None
                os.close(release_read)
                release_read = None
                self.assertEqual(os.read(ready_read, 5), b"ready")

            competing = factory_execution.FactoryRecordStore(
                str(self.records)
            )
            with self.assertRaises(BlockingIOError):
                competing.__enter__()
            os.write(release_write, b"x")
            self.assertEqual(process.wait(timeout=2), 0)
            with competing:
                self.assertIsNotNone(competing.lease_descriptor)
        finally:
            for descriptor in (
                ready_read,
                ready_write,
                release_read,
                release_write,
            ):
                if descriptor is not None:
                    os.close(descriptor)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    def test_factory_and_recovery_children_inherit_the_factory_lease(self):
        factory_runner = mock.Mock(
            return_value=types.SimpleNamespace(returncode=0)
        )
        recovery_runner = mock.Mock(
            return_value=types.SimpleNamespace(returncode=0)
        )
        grant = {
            "publication_nonce": "2" * 32,
            "session_token": "1" * 32,
        }
        authorization = self.engine().plan_complete_rebuild(self.ctx)
        architecture_command = tuple(
            item["argv"]
            for item in authorization.exact_commands
            if item.get("stage") == "target-architecture-observation"
        )[0]

        with factory_execution.FactoryRecordStore(
            str(self.records)
        ) as store:
            lease_descriptor = store.lease_descriptor
            factory_executor = factory_execution.RebuildCommandExecutor(
                self.ctx,
                authorization,
                runner=factory_runner,
                lease_descriptor=lease_descriptor,
            )
            factory_executor.execute_exact(
                "target-architecture-observation",
                architecture_command,
            )
            recovery_executor = factory_execution._RecoveryExecutor(
                self.ctx,
                grant,
                runner=recovery_runner,
                lease_descriptor=lease_descriptor,
            )
            recovery_executor._run(("umount", "-fl", self.ctx.fs_dir))

        factory_runner.assert_called_once_with(
            list(architecture_command),
            close_fds=True,
            pass_fds=(lease_descriptor,),
        )
        recovery_runner.assert_called_once_with(
            ["umount", "-fl", self.ctx.fs_dir],
            close_fds=True,
            pass_fds=(lease_descriptor,),
        )

    def test_executor_rejects_changed_architecture_and_candidate(self):
        authorization = self.engine().plan_complete_rebuild(self.ctx)
        executor = factory_execution.RebuildCommandExecutor(
            self.ctx,
            authorization,
            runner=mock.Mock(),
            mountinfo_reader=lambda: tuple(),
        )

        with self.assertRaises(factory_execution.FactoryExecutionError):
            executor.validate_architecture("i386")
        with self.assertRaises(factory_execution.FactoryExecutionError):
            executor.assert_publication_candidate(
                str(self.work / "foreign.candidate")
            )

    def test_executor_consumes_the_complete_exact_command_surface(self):
        authorization = self.engine().plan_complete_rebuild(self.ctx)

        def runner(command, **_options):
            if tuple(command) == authorization.b2a_plan.commands[0].argv:
                Path(authorization.bindings.probe_output).write_bytes(
                    b"synthetic probe"
                )
            return types.SimpleNamespace(returncode=0, stdout="amd64\n")

        executor = factory_execution.RebuildCommandExecutor(
            self.ctx,
            authorization,
            runner=runner,
            mountinfo_reader=lambda: tuple(),
        )
        architecture = next(
            item["argv"]
            for item in authorization.exact_commands
            if item["stage"] == "target-architecture-observation"
        )
        executor.execute_exact(
            "target-architecture-observation",
            architecture,
            stdout=-1,
            text=True,
        )
        for command in tuple(executor._mount_commands):
            executor._mount_runner(command)

        kernel_command = next(iter(executor._kernel_targets))
        prefix_length = len(
            factory_execution.FactoryExecutionEngine._chroot_prefix(
                self.ctx,
                authorization.tool_paths["chroot"],
            )
        )
        target = kernel_command[prefix_length:]
        service_target = str(self.work / "FileSystem/sbin/initctl")
        executor.begin_chroot_transaction(target, (service_target,))
        prefix = kernel_command[:prefix_length]
        executor.run(
            "chroot-locale",
            prefix + ("locale-gen", self.ctx.locales),
        )
        executor.run(
            "chroot-service-stub",
            prefix + ("ln", "-s", "/bin/true", "/sbin/initctl"),
        )
        for tail in (
            ("apt-get", "update", "-qq"),
            ("dpkg", "--configure", "-a"),
            ("apt-get", "install", "-f", "-y", "-q"),
        ):
            executor.run("chroot-apt-helper", prefix + tail)
        executor.run("chroot-target", kernel_command)
        for tail in (
            ("apt-get", "autoremove", "--purge"),
            ("apt-get", "autoclean"),
            ("apt-get", "clean"),
        ):
            executor.run("chroot-cleanup", prefix + tail)
        executor.end_chroot_transaction()

        executor.build_squashfs(
            str(self.work / "ISO/casper/filesystem.squashfs")
        )
        executor.execute_planned("manifest-query")
        executor.execute_planned("iso-generation", cwd=str(self.work / "ISO"))
        executor.execute_planned("legacy-isohybrid-mutation")
        executor.assert_complete()

        self.assertGreater(len(executor.records), 15)
        self.assertNotIn(str(self.root), json.dumps(executor.records))
        self.assertFalse(
            os.path.lexists(
                self.work / (".liveusb-compression-probe-" + "1" * 32)
            )
        )

    def test_executor_rejects_unjournaled_cleanup_and_service_targets(self):
        authorization = self.engine().plan_complete_rebuild(self.ctx)
        executor = factory_execution.RebuildCommandExecutor(
            self.ctx,
            authorization,
            runner=mock.Mock(return_value=types.SimpleNamespace(returncode=0)),
        )

        with self.assertRaises(factory_execution.FactoryExecutionError):
            executor._unmount_runner(
                (
                    authorization.tool_paths["umount"],
                    "-fl",
                    "/foreign/mount",
                )
            )
        target = next(iter(executor._kernel_targets))
        prefix_length = len(
            factory_execution.FactoryExecutionEngine._chroot_prefix(
                self.ctx,
                authorization.tool_paths["chroot"],
            )
        )
        with self.assertRaises(factory_execution.FactoryExecutionError):
            executor.begin_chroot_transaction(
                target[prefix_length:],
                ("/foreign/service",),
            )

    def test_grant_and_receipt_are_deeply_immutable(self):
        authorization = self.engine().plan_complete_rebuild(self.ctx)
        before = authorization.receipt.to_json()

        with self.assertRaises(TypeError):
            authorization.metadata["architecture"] = "i386"
        with self.assertRaises(TypeError):
            authorization.mutation_authority[0]["rollback"] = "changed"
        with self.assertRaises(TypeError):
            authorization.receipt.payload["decision"] = "refused"

        self.assertEqual(authorization.receipt.to_json(), before)

    def test_mount_publication_uses_the_exact_grant_namespace(self):
        iso = str(self.work / "ubuntuDE-amd64-14.04.iso")
        sidecar = str(self.work / "ubuntuDE-amd64-14.04.sha256")
        token = "1" * 32
        nonce = "2" * 32

        with mount_session.MountSession(
            self.ctx,
            mountinfo_reader=lambda: tuple(),
            mount_runner=lambda _command: True,
            unmount_runner=lambda _command: True,
            owner_token=token,
            mount_executable=str(self.bin_dir / "mount"),
            unmount_executable=str(self.bin_dir / "umount"),
        ) as session:
            view = session.begin_external_publication(
                iso,
                sidecar,
                "final-image",
                namespace_nonce=nonce,
            )
            self.assertEqual(session.token, token)
            self.assertEqual(
                view["primary_candidate"],
                str(
                    self.work
                    / (
                        ".liveusb-publish-{}-{}-primary.candidate".format(
                            token,
                            nonce,
                        )
                    )
                ),
            )

        self.assertFalse(os.path.lexists(view["primary_candidate"]))
        self.assertFalse(
            os.path.lexists(self.runtime_parent / "liveusb/mount-session.json")
        )

    def test_mount_session_rejects_invalid_external_tokens(self):
        with self.assertRaises(ValueError):
            mount_session.MountSession(
                self.ctx,
                owner_token="not-a-token",
            )


if __name__ == "__main__":
    unittest.main()
