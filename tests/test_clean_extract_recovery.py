from __future__ import annotations

import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import clean
from liveusb.backend import extract
from liveusb.backend import mounts
from liveusb.backend.mount_session import MountRecoveryError, MountSession

from tests.test_mount_session import FakeMountTable
from tests.test_mount_session import FakeXAccess


class _Result:
    def __init__(self, returncode):
        self.returncode = returncode


class CleanExtractRecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work_dir = self.root / "work"
        self.fs_dir = self.work_dir / "FileSystem"
        for relative in ("etc", "usr", "root", "tmp"):
            (self.fs_dir / relative).mkdir(
                parents=True,
                exist_ok=True,
            )
        self.mount_dir = self.root / "mount"
        self.mount_dir.mkdir()
        self.iso = self.root / "source.iso"
        self.iso.write_bytes(b"synthetic ISO")
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.mount_dir),
            runtime_dir=str(self.root / "runtime"),
            iso=str(self.iso),
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

    def abandon_owned_mounts(self):
        abandoned = self.session()
        abandoned.__enter__()
        abandoned.mount_sys()
        abandoned._release_runtime_lock()

    def test_clean_recovers_stale_session_before_workspace_purge(self):
        self.abandon_owned_mounts()
        events = []

        def purge(_ctx):
            self.assertEqual(self.table.identities, [])
            events.append("purge")

        with mock.patch.object(
            clean.mount_session,
            "MountSession",
            side_effect=lambda _ctx: self.session(),
        ), mock.patch.object(
            clean.mounts,
            "purge_work_dirs",
            side_effect=purge,
        ):
            clean.run_clean(self.ctx)

        self.assertEqual(events, ["purge"])
        self.assertEqual(self.table.identities, [])

    def test_extract_recovers_stale_session_before_locked_body(self):
        self.abandon_owned_mounts()
        events = []

        def locked_body(_ctx, _session):
            self.assertEqual(self.table.identities, [])
            events.append("extract-body")

        with mock.patch.object(
            extract.mount_session,
            "MountSession",
            side_effect=lambda _ctx: self.session(),
        ), mock.patch.object(
            extract,
            "_run_extract_locked",
            side_effect=locked_body,
        ):
            extract.run_extract(self.ctx)

        self.assertEqual(events, ["extract-body"])
        self.assertEqual(self.table.identities, [])

    def test_stale_exact_iso_mount_is_recovered_by_fresh_session(self):
        abandoned = self.session()
        abandoned.__enter__()
        acquisition = abandoned.mount_iso()
        mount_point = Path(acquisition.destination)
        abandoned._release_runtime_lock()

        self.assertTrue(mount_point.is_dir())
        self.assertEqual(len(self.table.identities), 1)
        journal = json.loads(
            (
                self.root / "runtime/mount-session.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            journal["directories"][-1]["path"],
            str(mount_point),
        )
        self.assertEqual(
            journal["directories"][-1]["identity"]["ino"],
            os.lstat(mount_point).st_ino,
        )

        with self.session():
            pass

        self.assertEqual(self.table.identities, [])
        self.assertFalse(mount_point.exists())
        self.assertFalse(
            (self.root / "runtime/mount-session.json").exists()
        )
        self.assertEqual(
            self.table.unmount_commands,
            [["umount", "-f", str(mount_point)]],
        )

    def test_stale_iso_recovery_is_independent_of_current_iso(self):
        abandoned = self.session()
        abandoned.__enter__()
        acquisition = abandoned.mount_iso()
        mount_point = Path(acquisition.destination)
        abandoned._release_runtime_lock()
        replacement = self.root / "replacement.iso"
        replacement.write_bytes(b"different synthetic ISO")
        self.ctx.iso = str(replacement)

        with self.session():
            pass

        self.assertFalse(mount_point.exists())
        self.assertEqual(len(self.table.unmount_commands), 1)

    def test_changed_iso_source_preserves_custody_without_unmount(self):
        abandoned = self.session()
        abandoned.__enter__()
        acquisition = abandoned.mount_iso()
        abandoned._release_runtime_lock()
        self.iso.write_bytes(b"replaced ISO content")
        journal = self.root / "runtime/mount-session.json"
        before = journal.read_bytes()

        with self.assertRaises(MountRecoveryError):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(journal.read_bytes(), before)
        self.assertTrue(Path(acquisition.destination).exists())

    def test_interrupted_iso_mount_exact_delta_is_recovered(self):
        def interrupt_after_mount(command):
            self.table.mount_commands.append(list(command))
            self.table.add(
                command[-1],
                source="/dev/loop-test",
                fs_type="iso9660",
                mount_options=("ro",),
                super_options=("ro",),
            )
            raise KeyboardInterrupt("synthetic ISO mount interruption")

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
            abandoned.mount_iso()
        mount_point = Path(
            abandoned._state["mounts"][0]["destination"]
        )
        abandoned._release_runtime_lock()

        with self.session():
            pass

        self.assertFalse(mount_point.exists())
        self.assertEqual(len(self.table.unmount_commands), 1)

    def test_interrupted_iso_directory_creation_is_recovered(self):
        abandoned = self.session()
        abandoned.__enter__()
        real_mkdir = os.mkdir

        def create_then_interrupt(path, mode):
            real_mkdir(path, mode)
            raise KeyboardInterrupt("synthetic ISO mkdir interruption")

        with mock.patch.object(
            os,
            "mkdir",
            side_effect=create_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                abandoned.mount_iso()
        destination = Path(
            abandoned._state["mounts"][0]["destination"]
        )
        abandoned._release_runtime_lock()

        with self.session():
            pass

        self.assertFalse(destination.exists())
        self.assertEqual(self.table.mount_commands, [])

    def test_iso_mountpoint_is_private(self):
        session = self.session()
        session.__enter__()
        acquisition = session.mount_iso()

        self.assertEqual(
            os.stat(acquisition.destination).st_mode & 0o777,
            0o700,
        )
        session.cleanup()
        session._release_runtime_lock()

    def test_iso_destination_rejects_symlinked_ancestor(self):
        real_root = self.root / "real-mount"
        real_root.mkdir()
        linked_root = self.root / "linked-mount"
        linked_root.symlink_to(real_root, target_is_directory=True)
        self.ctx.mount_dir = str(linked_root)

        with self.assertRaises(mounts.MountEvidenceError):
            mounts.validate_iso_mount_destination(
                self.ctx,
                str(linked_root / "candidate"),
            )

    def test_iso_destination_rejects_nested_and_foreign_paths(self):
        candidates = (
            self.mount_dir,
            self.mount_dir / "nested" / "candidate",
            self.root / "foreign" / "candidate",
            self.mount_dir / ".." / "escape",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(mounts.MountEvidenceError):
                    mounts.validate_iso_mount_destination(
                        self.ctx,
                        str(candidate),
                    )

    def test_iso_custody_rejects_replaced_mount_root(self):
        request = mounts.iso_mount_request(
            self.ctx,
            str(self.mount_dir / "candidate"),
        )
        moved = self.root / "old-mount"
        self.mount_dir.rename(moved)
        self.mount_dir.mkdir()

        with self.assertRaisesRegex(
            mounts.MountEvidenceError,
            "custody changed",
        ):
            mounts.validate_iso_custody(request)

    def test_replaced_iso_mount_is_preserved_without_unmount(self):
        abandoned = self.session()
        abandoned.__enter__()
        acquisition = abandoned.mount_iso()
        self.table.replace_path(acquisition.destination)
        abandoned._release_runtime_lock()
        journal = self.root / "runtime/mount-session.json"
        journal_before = journal.read_bytes()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "identity changed",
        ):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(journal.read_bytes(), journal_before)
        self.assertTrue(Path(acquisition.destination).exists())

    def test_ambiguous_iso_mount_is_preserved_without_unmount(self):
        abandoned = self.session()
        abandoned.__enter__()
        acquisition = abandoned.mount_iso()
        self.table.add(
            acquisition.destination,
            source="/ambiguous",
        )
        abandoned._release_runtime_lock()

        with self.assertRaisesRegex(
            MountRecoveryError,
            "identity changed",
        ):
            self.session().__enter__()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(
            len(
                mounts.mounts_at(
                    self.table.identities,
                    acquisition.destination,
                )
            ),
            2,
        )

    def test_overlapping_mount_and_workspace_is_rejected_first(self):
        context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.work_dir / "mount"),
            runtime_dir=str(self.root / "overlap-runtime"),
            iso=str(self.iso),
        )
        with mock.patch.object(
            extract.mount_session,
            "MountSession",
        ) as session_factory:
            with self.assertRaisesRegex(
                mounts.MountEvidenceError,
                "overlap",
            ):
                extract.run_extract(context)

        session_factory.assert_not_called()
        self.assertFalse((self.root / "overlap-runtime").exists())

    def test_invalid_image_purges_only_after_iso_cleanup(self):
        events = []

        def purge(_ctx):
            events.append(
                (
                    "purge",
                    len(self.table.identities),
                    tuple(self.mount_dir.iterdir()),
                )
            )

        with mock.patch.object(
            extract.mount_session,
            "MountSession",
            side_effect=lambda _ctx: self.session(),
        ), mock.patch.object(
            extract,
            "_clean",
            side_effect=purge,
        ), mock.patch.object(
            extract.chroot,
            "create_work_dirs",
        ):
            with self.assertRaisesRegex(
                Exception,
                "not a usable image",
            ):
                extract.run_extract(self.ctx)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1][1], 0)
        self.assertEqual(events[-1][2], ())
        self.assertEqual(len(self.table.unmount_commands), 1)

    def test_failed_iso_cleanup_suppresses_failure_purge(self):
        events = []

        def failing_unmount(command):
            self.table.unmount_commands.append(list(command))
            return False

        session = MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=failing_unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )
        with mock.patch.object(
            extract.mount_session,
            "MountSession",
            return_value=session,
        ), mock.patch.object(
            extract,
            "_clean",
            side_effect=lambda _ctx: events.append("purge"),
        ), mock.patch.object(
            extract.chroot,
            "create_work_dirs",
        ):
            with self.assertRaises(Exception):
                extract.run_extract(self.ctx)

        self.assertEqual(events, ["purge"])
        self.assertTrue(self.table.identities)
        self.assertEqual(len(self.table.unmount_commands), 1)

    def test_architecture_mismatch_purges_after_cleanup(self):
        shutil.rmtree(self.work_dir)
        events = []

        def create_work_dirs(_ctx):
            for relative in ("FileSystem", "ISO"):
                (self.work_dir / relative).mkdir(
                    parents=True,
                    exist_ok=True,
                )

        real_isdir = extract.os.path.isdir

        def image_layout(path):
            if "liveusb-iso-" in os.fspath(path):
                return True
            return real_isdir(path)

        results = (
            types.SimpleNamespace(
                returncode=0,
                stdout="amd64\n",
            ),
            types.SimpleNamespace(
                returncode=0,
                stdout="aarch64\n",
            ),
        )
        with mock.patch.object(
            extract.mount_session,
            "MountSession",
            side_effect=lambda _ctx: self.session(),
        ), mock.patch.object(
            extract,
            "_clean",
            side_effect=lambda _ctx: events.append(
                ("purge", len(self.table.identities))
            ),
        ), mock.patch.object(
            extract.chroot,
            "create_work_dirs",
            side_effect=create_work_dirs,
        ), mock.patch.object(
            extract.os.path,
            "isdir",
            side_effect=image_layout,
        ), mock.patch.object(
            extract.os.path,
            "exists",
            return_value=True,
        ), mock.patch.object(
            extract,
            "run",
            return_value=_Result(0),
        ), mock.patch.object(
            extract.subprocess,
            "run",
            side_effect=results,
        ):
            with self.assertRaisesRegex(
                Exception,
                "architecture mismatch",
            ):
                extract.run_extract(self.ctx)

        self.assertEqual(events, [("purge", 0)])
        self.assertEqual(self.table.identities, [])

    def test_no_blind_recursive_unmount_exists_in_cleanup_sources(self):
        clean_source = Path(clean.__file__).read_text(
            encoding="utf-8"
        )
        extract_source = Path(extract.__file__).read_text(
            encoding="utf-8"
        )

        self.assertNotIn("recursive_umount", clean_source)
        self.assertNotIn("recursive_umount", extract_source)
        self.assertNotIn('["umount", "-fl"', extract_source)
        self.assertNotIn("subprocess.run([\"umount\"", extract_source)


if __name__ == "__main__":
    unittest.main()
