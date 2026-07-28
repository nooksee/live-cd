from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import mount_session
from liveusb.backend.mount_session import (
    MountRecoveryError,
    MountSession,
    MountSessionCleanupError,
)

from tests.test_mount_session import FakeMountTable
from tests.test_mount_session import FakeXAccess


@unittest.skipUnless(
    hasattr(os, "fork"),
    "POSIX fork is unavailable",
)
class MountRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work_dir = self.root / "work"
        self.fs_dir = self.work_dir / "FileSystem"
        self.runtime_dir = self.root / "runtime"
        for relative in ("etc", "usr", "root", "tmp"):
            (self.fs_dir / relative).mkdir(
                parents=True,
                exist_ok=True,
            )
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(self.runtime_dir),
        )
        self.table = FakeMountTable()
        self.x_access = FakeXAccess(
            enabled=False,
            local_present=False,
        )

    def session(self):
        return MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

    def abandon(self, session):
        session._release_runtime_lock()

    @property
    def journal_path(self):
        return self.runtime_dir / "mount-session.json"

    def pending_paths(self):
        return tuple(
            self.runtime_dir.glob("mount-session.json.pending-*")
        )

    def staged_paths(self):
        return tuple((self.fs_dir / "tmp").glob("liveusb-*"))

    def assert_hard_recovery_clean(self):
        self.assertFalse(self.journal_path.exists())
        self.assertEqual(self.pending_paths(), ())
        self.assertEqual(self.staged_paths(), ())
        self.assertTrue(
            (self.runtime_dir / "operation.lock").is_file()
        )

    def test_interrupted_initial_journal_is_recovered(self):
        interrupted = self.session()
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError(
                "synthetic initial journal interruption"
            ),
        ):
            with self.assertRaisesRegex(
                OSError,
                "initial journal interruption",
            ):
                interrupted.__enter__()

        self.assertFalse(self.journal_path.exists())
        self.assertEqual(len(self.pending_paths()), 1)

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_noninitial_predecessorless_pending_is_preserved(self):
        interrupted = self.session()
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError("synthetic initial interruption"),
        ):
            with self.assertRaises(OSError):
                interrupted.__enter__()
        pending = self.pending_paths()[0]
        candidate = json.loads(
            pending.read_text(encoding="utf-8")
        )
        candidate["phase"] = "cleaning"
        pending.write_bytes(
            MountSession._encode_json(candidate)
        )
        pending.chmod(0o600)
        pending_before = pending.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "not an initial state",
        ):
            self.session().__enter__()

        self.assertFalse(self.journal_path.exists())
        self.assertEqual(pending.read_bytes(), pending_before)
        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(self.x_access.mutations, [])

    def test_command_started_to_owned_pending_is_recovered(self):
        abandoned = self.session()
        abandoned.__enter__()
        real_replace = mount_session.os.replace

        def interrupt_owned(source, destination):
            if (
                abandoned._state["mounts"]
                and abandoned._state["mounts"][-1]["stage"]
                == "owned"
            ):
                raise OSError(
                    "synthetic owned persistence interruption"
                )
            return real_replace(source, destination)

        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=interrupt_owned,
        ):
            with self.assertRaisesRegex(
                OSError,
                "owned persistence interruption",
            ):
                abandoned.mount_sys()
        self.abandon(abandoned)
        self.assertEqual(len(self.pending_paths()), 1)

        with self.session():
            pass

        self.assertEqual(self.table.identities, [])
        self.assert_hard_recovery_clean()

    def test_pending_planned_to_owned_mount_is_rejected(self):
        abandoned = self.session()
        abandoned.__enter__()
        with mock.patch.object(
            abandoned,
            "_ensure_directory",
            side_effect=KeyboardInterrupt(
                "synthetic planned mount pause"
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                abandoned.mount_sys()
        identity = self.table.add(
            self.fs_dir / "dev",
            source="/dev",
        )
        plan = abandoned._state["mounts"][0]
        plan["observed_after"] = [identity.to_record()]
        plan["owned"] = [
            {
                "identity": identity.to_record(),
                "inferred": False,
                "stage": "owned",
            }
        ]
        plan["stage"] = "owned"
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError(
                "synthetic forbidden mount pending"
            ),
        ):
            with self.assertRaises(OSError):
                abandoned._persist_journal()
        self.abandon(abandoned)
        journal_before = self.journal_path.read_bytes()
        pending = self.pending_paths()[0]
        pending_before = pending.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "mount stage transition is invalid",
        ):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(self.x_access.mutations, [])
        self.assertEqual(self.journal_path.read_bytes(), journal_before)
        self.assertEqual(pending.read_bytes(), pending_before)

    def test_pending_unexamined_to_owned_x_is_rejected(self):
        abandoned = self.session()
        abandoned.__enter__()
        abandoned._state["x"] = {
            "before": {
                "enabled": True,
                "local_present": False,
            },
            "mutation": True,
            "stage": "owned",
        }
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError(
                "synthetic forbidden X pending"
            ),
        ):
            with self.assertRaises(OSError):
                abandoned._persist_journal()
        self.abandon(abandoned)
        journal_before = self.journal_path.read_bytes()
        pending = self.pending_paths()[0]
        pending_before = pending.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "X stage transition is invalid",
        ):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(self.x_access.mutations, [])
        self.assertEqual(self.journal_path.read_bytes(), journal_before)
        self.assertEqual(pending.read_bytes(), pending_before)

    def test_child_os_exit_is_recovered_by_fresh_instance(self):
        source = self.root / "source.deb"
        source.write_bytes(b"child crash payload")
        child = os.fork()
        if child == 0:
            session = self.session()
            session.__enter__()
            session.stage_file(
                str(source),
                "deb",
                suffix=".deb",
            )
            os._exit(0)

        waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertTrue(os.WIFEXITED(status))
        self.assertEqual(os.WEXITSTATUS(status), 0)
        self.assertTrue(self.journal_path.is_file())
        self.assertEqual(len(self.staged_paths()), 1)

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_abandoned_owned_mounts_are_recovered_exactly(self):
        self.table.nested_on_rbind = True
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.mount_sys()
        self.abandon(abandoned)

        with self.session():
            pass

        self.assertEqual(
            tuple(
                command[-1]
                for command in self.table.unmount_commands
            ),
            (
                str(self.fs_dir / "sys"),
                str(self.fs_dir / "proc"),
                str(self.fs_dir / "dev/pts"),
                str(self.fs_dir / "dev"),
            ),
        )
        self.assertEqual(self.table.identities, [])
        self.assert_hard_recovery_clean()

    def test_interrupted_recovery_is_retryable_by_another_instance(self):
        source = self.root / "source.deb"
        source.write_bytes(b"retry payload")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        artifact = self.staged_paths()[0]
        self.abandon(abandoned)
        real_unlink = os.unlink
        failed = {"value": False}

        def fail_artifact_once(path):
            if (
                os.path.abspath(path) == str(artifact)
                and not failed["value"]
            ):
                failed["value"] = True
                raise OSError("synthetic recovery interruption")
            return real_unlink(path)

        with mock.patch.object(
            mount_session.os,
            "unlink",
            side_effect=fail_artifact_once,
        ):
            with self.assertRaises(MountSessionCleanupError):
                self.session().__enter__()

        self.assertTrue(self.journal_path.is_file())
        self.assertTrue(artifact.is_file())

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_unmount_failure_is_retried_from_durable_progress(self):
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.mount_sys()
        self.abandon(abandoned)
        failed_path = str(self.fs_dir / "sys")
        self.table.fail_unmount_paths.add(failed_path)

        with self.assertRaises(MountSessionCleanupError):
            self.session().__enter__()

        self.assertTrue(self.journal_path.is_file())
        self.assertIn(
            failed_path,
            tuple(
                identity.mount_point
                for identity in self.table.identities
            ),
        )

        self.table.fail_unmount_paths.clear()
        with self.session():
            pass

        self.assertEqual(self.table.identities, [])
        self.assert_hard_recovery_clean()

    def test_planned_directory_exact_end_state_is_recovered(self):
        os.rmdir(self.fs_dir / "tmp")
        source = self.root / "source.deb"
        source.write_bytes(b"directory crash payload")
        abandoned = self.session()
        abandoned.__enter__()
        real_mkdir = os.mkdir

        def create_then_interrupt(path, mode):
            real_mkdir(path, mode)
            raise RuntimeError("synthetic mkdir interruption")

        with mock.patch.object(
            mount_session.os,
            "mkdir",
            side_effect=create_then_interrupt,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic mkdir interruption",
            ):
                abandoned.stage_file(
                    str(source),
                    "deb",
                    suffix=".deb",
                )
        self.abandon(abandoned)

        self.assertTrue((self.fs_dir / "tmp").is_dir())
        with self.session():
            pass

        self.assertFalse((self.fs_dir / "tmp").exists())
        self.assert_hard_recovery_clean()

    def test_corrupt_journal_is_preserved_byte_identically(self):
        self.runtime_dir.mkdir(mode=0o700)
        raw = b"{corrupt journal\n"
        self.journal_path.write_bytes(raw)
        self.journal_path.chmod(0o600)

        with self.assertRaisesRegex(
            MountRecoveryError,
            "corrupt",
        ):
            self.session().__enter__()

        self.assertEqual(self.journal_path.read_bytes(), raw)
        self.assertEqual(self.table.unmount_commands, [])

    def test_pending_journal_is_preserved_without_mutation(self):
        source = self.root / "source.deb"
        source.write_bytes(b"pending payload")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        self.abandon(abandoned)
        pending = (
            self.runtime_dir
            / "mount-session.json.pending-synthetic"
        )
        pending.write_bytes(b"pending evidence")
        pending.chmod(0o600)
        journal_before = self.journal_path.read_bytes()
        artifact_before = self.staged_paths()[0].read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "Pending",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(pending.read_bytes(), b"pending evidence")
        self.assertEqual(
            self.staged_paths()[0].read_bytes(),
            artifact_before,
        )
        self.assertEqual(self.table.unmount_commands, [])

    def test_journal_path_escape_preserves_all_evidence(self):
        source = self.root / "source.deb"
        source.write_bytes(b"path escape payload")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        self.abandon(abandoned)
        data = json.loads(
            self.journal_path.read_text(encoding="utf-8")
        )
        escaped = self.root / "outside-artifact"
        escaped.write_bytes(b"outside")
        data["artifacts"][0]["path"] = str(escaped)
        self.journal_path.write_text(
            json.dumps(data, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "escapes FileSystem",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(escaped.read_bytes(), b"outside")
        self.assertEqual(self.table.unmount_commands, [])

    def test_recovery_rejects_a_different_canonical_workspace(self):
        source = self.root / "source.deb"
        source.write_bytes(b"root binding payload")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        self.abandon(abandoned)
        journal_before = self.journal_path.read_bytes()
        other_work = self.root / "other-work"
        for relative in ("etc", "usr", "root", "tmp"):
            (other_work / "FileSystem" / relative).mkdir(
                parents=True,
                exist_ok=True,
            )
        other_context = Context(
            work_dir=str(other_work),
            mount_dir=str(self.root / "other-mount"),
            runtime_dir=str(self.runtime_dir),
        )
        other = MountSession(
            other_context,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

        with self.assertRaisesRegex(
            MountRecoveryError,
            "roots do not match",
        ):
            other.__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(self.table.unmount_commands, [])

    def test_identity_replacement_blocks_all_recovery_mutation(self):
        source = self.root / "source.deb"
        source.write_bytes(b"original artifact")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.mount_sys()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        artifact = self.staged_paths()[0]
        self.abandon(abandoned)
        replacement = self.root / "replacement"
        replacement.write_bytes(b"replacement artifact")
        os.replace(str(replacement), str(artifact))
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "identity changed",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(
            artifact.read_bytes(),
            b"replacement artifact",
        )
        self.assertEqual(self.table.unmount_commands, [])

    def test_symlinked_recovery_parent_escape_is_preserved(self):
        source = self.root / "source.deb"
        source.write_bytes(b"parent escape payload")
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        self.abandon(abandoned)
        escaped_parent = self.root / "escaped-tmp"
        os.rename(
            str(self.fs_dir / "tmp"),
            str(escaped_parent),
        )
        os.symlink(
            str(escaped_parent),
            str(self.fs_dir / "tmp"),
        )
        artifact = tuple(escaped_parent.glob("liveusb-*"))[0]
        journal_before = self.journal_path.read_bytes()
        artifact_before = artifact.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "parent escapes FileSystem",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(artifact.read_bytes(), artifact_before)
        self.assertEqual(self.table.unmount_commands, [])

    def test_runtime_lock_identity_replacement_blocks_resource_mutation(self):
        source = self.root / "source.deb"
        source.write_bytes(b"lock identity payload")
        session = self.session()
        session.__enter__()
        journal_before = self.journal_path.read_bytes()
        lock_path = self.runtime_dir / "operation.lock"
        os.unlink(lock_path)
        lock_path.write_bytes(b"replacement lock inode")

        with self.assertRaisesRegex(
            MountRecoveryError,
            "lock identity changed",
        ):
            session.stage_file(
                str(source),
                "deb",
                suffix=".deb",
            )

        with self.assertRaises(MountSessionCleanupError):
            session.__exit__(None, None, None)

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(self.staged_paths(), ())
        self.assertEqual(self.table.mount_commands, [])

    def test_interrupted_mount_with_changed_evidence_is_not_adopted(self):
        def interrupt_after_mount(command):
            self.table.mount(command)
            raise KeyboardInterrupt(
                "synthetic post-mount interruption"
            )

        abandoned = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=interrupt_after_mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )
        abandoned.__enter__()
        with self.assertRaises(KeyboardInterrupt):
            abandoned.mount_sys()
        self.abandon(abandoned)
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "ownership is ambiguous",
        ):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.table.remove_path(self.fs_dir / "dev")

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_interrupted_mount_exact_delta_is_recovered(self):
        source = self.table.add("/dev", source="/dev")

        def interrupt_after_mount(command):
            destination = os.path.abspath(command[-1])
            self.table.mount_commands.append(list(command))
            self.table.add(
                destination,
                source=source.source,
                major_minor=source.major_minor,
                root=source.root,
                fs_type=source.fs_type,
                mount_options=source.mount_options,
                super_options=source.super_options,
            )
            raise KeyboardInterrupt("synthetic post-mount interruption")

        abandoned = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=interrupt_after_mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )
        abandoned.__enter__()
        with self.assertRaises(KeyboardInterrupt):
            abandoned.mount_sys()
        self.abandon(abandoned)

        with self.session():
            pass

        self.assertEqual(
            tuple(identity.mount_point for identity in self.table.identities),
            ("/dev",),
        )
        self.assertEqual(len(self.table.unmount_commands), 1)
        self.assert_hard_recovery_clean()

    def test_planned_directory_ambiguous_mode_is_preserved(self):
        os.rmdir(self.fs_dir / "tmp")
        source = self.root / "source.deb"
        source.write_bytes(b"directory crash payload")
        abandoned = self.session()
        abandoned.__enter__()
        real_mkdir = os.mkdir

        def create_then_interrupt(path, mode):
            real_mkdir(path, 0o700)
            raise RuntimeError("synthetic mkdir interruption")

        with mock.patch.object(
            mount_session.os,
            "mkdir",
            side_effect=create_then_interrupt,
        ):
            with self.assertRaises(RuntimeError):
                abandoned.stage_file(str(source), "deb", suffix=".deb")
        self.abandon(abandoned)
        before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "ambiguous",
        ):
            self.session().__enter__()

        self.assertEqual(self.journal_path.read_bytes(), before)

    def test_interrupted_mount_at_prestate_resolves_without_ownership(self):
        def interrupt_without_mount(_command):
            raise KeyboardInterrupt(
                "synthetic pre-mount interruption"
            )

        abandoned = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=interrupt_without_mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )
        abandoned.__enter__()
        with self.assertRaises(KeyboardInterrupt):
            abandoned.mount_sys()
        self.abandon(abandoned)

        with self.session():
            pass

        self.assertEqual(self.table.unmount_commands, [])
        self.assert_hard_recovery_clean()

    def test_planned_mount_at_prestate_resolves_without_ownership(self):
        abandoned = self.session()
        abandoned.__enter__()
        with mock.patch.object(
            abandoned,
            "_ensure_directory",
            side_effect=KeyboardInterrupt(
                "synthetic pre-directory interruption"
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                abandoned.mount_sys()
        self.abandon(abandoned)

        with self.session():
            pass

        self.assertEqual(self.table.mount_commands, [])
        self.assertEqual(self.table.unmount_commands, [])
        self.assert_hard_recovery_clean()

    def test_partial_writing_artifact_is_recovered_by_exact_inode(self):
        payload = b"partial child payload"
        source = self.root / "source.deb"
        source.write_bytes(payload)
        child = os.fork()
        if child == 0:
            session = self.session()
            session.__enter__()
            real_write_all = session._write_all

            def interrupt_copy(descriptor, raw):
                if raw == payload:
                    os.write(descriptor, raw[:5])
                    os._exit(0)
                return real_write_all(descriptor, raw)

            with mock.patch.object(
                session,
                "_write_all",
                side_effect=interrupt_copy,
            ):
                session.stage_file(
                    str(source),
                    "deb",
                    suffix=".deb",
                )
            os._exit(3)

        waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertEqual(os.WEXITSTATUS(status), 0)
        self.assertEqual(len(self.staged_paths()), 1)

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_planned_artifact_end_state_is_not_adopted(self):
        source = self.root / "source.deb"
        source.write_bytes(b"planned artifact")
        child = os.fork()
        if child == 0:
            session = self.session()
            session.__enter__()
            with mock.patch.object(
                session,
                "_capture_descriptor_identity",
                side_effect=lambda _descriptor: os._exit(0),
            ):
                session.stage_file(
                    str(source),
                    "deb",
                    suffix=".deb",
                )
            os._exit(3)

        waited, status = os.waitpid(child, 0)
        self.assertEqual(waited, child)
        self.assertEqual(os.WEXITSTATUS(status), 0)
        artifact = self.staged_paths()[0]
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "end state is unproved",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        os.unlink(artifact)

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_grant_planned_x_state_is_not_adopted(self):
        x_access = FakeXAccess(
            enabled=True,
            local_present=False,
        )
        abandoned = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=x_access.query,
            x_mutator=x_access.mutate,
        )
        abandoned.__enter__()
        real_persist = abandoned._persist_journal

        def interrupt_owned_persist():
            if abandoned._state["x"]["stage"] == "owned":
                raise KeyboardInterrupt(
                    "synthetic post-grant interruption"
                )
            return real_persist()

        with mock.patch.object(
            abandoned,
            "_persist_journal",
            side_effect=interrupt_owned_persist,
        ):
            with self.assertRaises(KeyboardInterrupt):
                abandoned.allow_local_x_access()
        self.abandon(abandoned)
        journal_before = self.journal_path.read_bytes()
        recovery = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=x_access.query,
            x_mutator=x_access.mutate,
        )

        with self.assertRaisesRegex(
            MountRecoveryError,
            "ownership is unproved",
        ):
            recovery.__enter__()

        self.assertEqual(x_access.mutations, [True])
        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        x_access.state = mount_session.mounts.XAccessState(
            enabled=True,
            local_present=False,
        )

        with MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=x_access.query,
            x_mutator=x_access.mutate,
        ):
            pass

        self.assert_hard_recovery_clean()

    def test_recorded_x_ownership_is_restored_by_fresh_instance(self):
        x_access = FakeXAccess(
            enabled=True,
            local_present=False,
        )
        abandoned = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=x_access.query,
            x_mutator=x_access.mutate,
        )
        abandoned.__enter__()
        abandoned.allow_local_x_access()
        self.abandon(abandoned)

        with MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=x_access.query,
            x_mutator=x_access.mutate,
        ):
            pass

        self.assertEqual(x_access.mutations, [True, False])
        self.assertFalse(x_access.state.local_present)
        self.assert_hard_recovery_clean()

    def test_simultaneous_child_process_holds_runtime_flock(self):
        ready_read, ready_write = os.pipe()
        release_read, release_write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(ready_read)
            os.close(release_write)
            session = self.session()
            session.__enter__()
            os.write(ready_write, b"1")
            os.read(release_read, 1)
            session.__exit__(None, None, None)
            os._exit(0)

        os.close(ready_write)
        os.close(release_read)
        try:
            self.assertEqual(os.read(ready_read, 1), b"1")
            with self.assertRaisesRegex(
                MountRecoveryError,
                "holds the runtime lock",
            ):
                self.session().__enter__()
            os.write(release_write, b"1")
            waited, status = os.waitpid(child, 0)
            self.assertEqual(waited, child)
            self.assertEqual(os.WEXITSTATUS(status), 0)
        finally:
            os.close(ready_read)
            os.close(release_write)

        self.assert_hard_recovery_clean()

    def test_valid_next_sequence_pending_journal_is_reconciled(self):
        abandoned = self.session()
        abandoned.__enter__()
        abandoned._state["phase"] = "cleaning"
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError("synthetic replace interruption"),
        ):
            with self.assertRaisesRegex(
                OSError,
                "synthetic replace interruption",
            ):
                abandoned._persist_journal()
        self.abandon(abandoned)
        self.assertEqual(len(self.pending_paths()), 1)

        with self.session():
            pass

        self.assert_hard_recovery_clean()

    def test_multiple_pending_journals_are_preserved(self):
        abandoned = self.session()
        abandoned.__enter__()
        self.abandon(abandoned)
        first = (
            self.runtime_dir
            / "mount-session.json.pending-first"
        )
        second = (
            self.runtime_dir
            / "mount-session.json.pending-second"
        )
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        first.chmod(0o600)
        second.chmod(0o600)
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "multiple or ambiguous",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")

    def test_mismatched_next_sequence_pending_is_preserved(self):
        abandoned = self.session()
        abandoned.__enter__()
        abandoned._state["phase"] = "cleaning"
        with mock.patch.object(
            mount_session.os,
            "replace",
            side_effect=OSError("synthetic replace interruption"),
        ):
            with self.assertRaises(OSError):
                abandoned._persist_journal()
        self.abandon(abandoned)
        pending = self.pending_paths()[0]
        candidate = json.loads(
            pending.read_text(encoding="utf-8")
        )
        candidate["previous_sha256"] = "0" * 64
        pending.write_bytes(
            mount_session.MountSession._encode_json(candidate)
        )
        pending.chmod(0o600)
        journal_before = self.journal_path.read_bytes()
        pending_before = pending.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "does not prove the next sequence",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.assertEqual(pending.read_bytes(), pending_before)

    def test_writer_rejects_metadata_larger_than_reader_limit(self):
        session = self.session()
        session.__enter__()
        oversized = b"x" * (
            mount_session._MAX_METADATA_BYTES + 1
        )

        with mock.patch.object(
            session,
            "_encode_json",
            return_value=oversized,
        ):
            with self.assertRaisesRegex(
                MountRecoveryError,
                "writer limit",
            ):
                session._persist_journal()

        self.assertEqual(self.pending_paths(), ())
        session.__exit__(None, None, None)
        self.assert_hard_recovery_clean()

    def test_runtime_directory_requires_mode_0700(self):
        self.runtime_dir.mkdir(mode=0o755)

        with self.assertRaisesRegex(
            MountRecoveryError,
            "custody directory is invalid",
        ):
            self.session().__enter__()

        self.assertFalse(
            (self.runtime_dir / "operation.lock").exists()
        )

    def test_missing_runtime_parent_creates_no_paths(self):
        missing_parent = self.root / "missing-parent"
        context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(missing_parent / "runtime"),
        )
        session = MountSession(
            context,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

        with self.assertRaisesRegex(
            MountRecoveryError,
            "parent chain is incomplete",
        ):
            session.__enter__()

        self.assertFalse(missing_parent.exists())

    def test_unsafe_writable_runtime_ancestor_is_rejected(self):
        unsafe_parent = self.root / "unsafe-parent"
        unsafe_parent.mkdir(mode=0o777)
        unsafe_parent.chmod(0o777)
        context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(unsafe_parent / "runtime"),
        )
        session = MountSession(
            context,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

        with self.assertRaisesRegex(
            MountRecoveryError,
            "unsafe writable ancestor",
        ):
            session.__enter__()

        self.assertFalse((unsafe_parent / "runtime").exists())

    def test_private_runtime_parent_accepts_private_leaf(self):
        private_parent = self.root / "private-parent"
        private_parent.mkdir(mode=0o700)
        context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(private_parent / "runtime"),
        )

        with MountSession(
            context,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        ):
            pass

        self.assertEqual(
            stat.S_IMODE(
                os.lstat(private_parent / "runtime").st_mode
            ),
            0o700,
        )

    def test_root_owned_sticky_run_lock_policy_is_accepted(self):
        session = self.session()
        states = {
            "/": stat.S_IFDIR | 0o755,
            "/run": stat.S_IFDIR | 0o755,
            "/run/lock": (
                stat.S_IFDIR | stat.S_ISVTX | 0o777
            ),
        }

        def fake_lstat(path):
            return types.SimpleNamespace(
                st_mode=states[path],
                st_uid=0,
            )

        with mock.patch.object(
            mount_session.os,
            "lstat",
            side_effect=fake_lstat,
        ):
            session._validate_runtime_parent_chain(
                "/run/lock"
            )

    def test_runtime_symlink_parent_is_rejected_before_creation(self):
        real_parent = self.root / "real-runtime-parent"
        real_parent.mkdir(mode=0o700)
        linked_parent = self.root / "linked-runtime-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        escaped_runtime = linked_parent / "runtime"
        context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(escaped_runtime),
        )
        session = MountSession(
            context,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

        with self.assertRaisesRegex(
            MountRecoveryError,
            "literal directory chain",
        ):
            session.__enter__()

        self.assertFalse((real_parent / "runtime").exists())

    def test_runtime_lock_requires_mode_0600_and_single_link(self):
        self.runtime_dir.mkdir(mode=0o700)
        lock_path = self.runtime_dir / "operation.lock"
        lock_path.write_bytes(b"lock")
        lock_path.chmod(0o644)

        with self.assertRaisesRegex(
            MountRecoveryError,
            "Runtime lock custody is invalid",
        ):
            self.session().__enter__()

        lock_path.chmod(0o600)
        hard_link = self.runtime_dir / "lock-link"
        os.link(lock_path, hard_link)
        with self.assertRaisesRegex(
            MountRecoveryError,
            "Runtime lock custody is invalid",
        ):
            self.session().__enter__()

    def test_journal_requires_mode_0600_and_single_link(self):
        abandoned = self.session()
        abandoned.__enter__()
        self.abandon(abandoned)
        self.journal_path.chmod(0o644)
        journal_before = self.journal_path.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "journal custody is invalid",
        ):
            self.session().__enter__()

        self.assertEqual(
            self.journal_path.read_bytes(),
            journal_before,
        )
        self.journal_path.chmod(0o600)
        hard_link = self.runtime_dir / "journal-link"
        os.link(self.journal_path, hard_link)

        with self.assertRaisesRegex(
            MountRecoveryError,
            "journal custody is invalid",
        ):
            self.session().__enter__()

    def test_persisted_legacy_dbus_plan_survives_topology_change(self):
        for path in (
            self.fs_dir / "var/lib/dbus",
            self.fs_dir / "var/run/dbus",
        ):
            path.mkdir(parents=True, exist_ok=True)
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.mount_dbus()
        self.abandon(abandoned)
        shutil.rmtree(self.fs_dir / "var/run")
        (self.fs_dir / "run/dbus").mkdir(parents=True)
        (self.fs_dir / "var/run").symlink_to(
            "../run",
            target_is_directory=True,
        )

        with self.session():
            pass

        self.assertEqual(self.table.identities, [])
        self.assert_hard_recovery_clean()


if __name__ == "__main__":
    unittest.main()
