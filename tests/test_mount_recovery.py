from __future__ import annotations

import json
import os
import tempfile
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

    def test_planned_directory_is_recovered_after_creation_interruption(self):
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
        self.runtime_dir.mkdir()
        raw = b"{corrupt journal\n"
        self.journal_path.write_bytes(raw)

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


if __name__ == "__main__":
    unittest.main()
