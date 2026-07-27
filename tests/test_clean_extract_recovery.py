from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import clean
from liveusb.backend import extract
from liveusb.backend import mounts
from liveusb.backend.mount_session import MountSession

from tests.test_mount_session import FakeMountTable
from tests.test_mount_session import FakeXAccess
from tests.test_mount_session import mount_identity


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
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(self.root / "runtime"),
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

        def locked_body(_ctx):
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

    def test_failed_iso_mount_with_changed_evidence_is_not_owned(self):
        mount_point = self.root / "mount/point"
        mount_point.mkdir(parents=True)
        external = mount_identity(
            90,
            mount_point,
            source="/external",
        )
        snapshots = iter(((), (external,)))
        commands = []

        def runner(command):
            commands.append(tuple(command))
            return _Result(1)

        with self.assertRaisesRegex(
            mounts.MountEvidenceError,
            "changed unowned evidence",
        ):
            extract._acquire_iso_mount(
                self.ctx,
                str(mount_point),
                mountinfo_reader=lambda: next(snapshots),
                runner=runner,
            )

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "mount")

    def test_iso_cleanup_rejects_replacement_without_unmount(self):
        mount_point = self.root / "mount/point"
        mount_point.mkdir(parents=True)
        owned = mount_identity(
            91,
            mount_point,
            source="/owned",
        )
        replacement = mount_identity(
            92,
            mount_point,
            source="/replacement",
        )
        commands = []

        with self.assertRaisesRegex(
            mounts.MountEvidenceError,
            "identity changed",
        ):
            extract._release_iso_mount(
                owned,
                mountinfo_reader=lambda: (replacement,),
                runner=lambda command: commands.append(command),
            )

        self.assertEqual(commands, [])

    def test_iso_cleanup_removes_only_the_exact_identity(self):
        mount_point = self.root / "mount/point"
        mount_point.mkdir(parents=True)
        owned = mount_identity(
            93,
            mount_point,
            source="/owned",
        )
        unrelated = mount_identity(
            94,
            self.root / "unrelated",
            source="/unrelated",
        )
        snapshots = iter(
            (
                (owned, unrelated),
                (unrelated,),
            )
        )
        commands = []

        extract._release_iso_mount(
            owned,
            mountinfo_reader=lambda: next(snapshots),
            runner=lambda command: (
                commands.append(tuple(command))
                or _Result(0)
            ),
        )

        self.assertEqual(
            commands,
            [("umount", "-f", str(mount_point))],
        )

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
