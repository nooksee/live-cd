from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import mount_session
from liveusb.backend import mounts
from liveusb.backend.mount_session import (
    MountAcquisitionError,
    MountSession,
    MountSessionCleanupError,
)


class MountSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.work_dir = self.root / "work"
        self.fs_dir = self.work_dir / "FileSystem"
        self.mount_dir = self.root / "mount"
        for directory in (
            self.fs_dir / "etc",
            self.fs_dir / "usr",
            self.fs_dir / "root",
            self.mount_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.mount_dir),
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _acquisition(
        source,
        destination,
        label,
        outcome,
        owned=False,
        created_directory=False,
        error=None,
    ):
        return mounts.MountAcquisition(
            source=source,
            destination=str(destination),
            label=label,
            outcome=outcome,
            owned=owned,
            created_directory=created_directory,
            error=error,
        )

    def test_mount_helpers_report_created_present_and_failed(self):
        dev = self.fs_dir / "dev"
        proc = self.fs_dir / "proc"
        sys = self.fs_dir / "sys"
        dev.mkdir()
        mounted = {str(dev)}
        commands = []

        def fake_run_ok(command):
            command = list(command)
            commands.append(command)
            destination = command[-1]
            if destination == str(proc):
                mounted.add(destination)
                return True
            return False

        with mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ), mock.patch.object(
            mounts,
            "run_ok",
            side_effect=fake_run_ok,
        ), mock.patch.object(
            mounts.messages,
            "extra_error_no_exit",
        ):
            results = mounts.mount_sys(self.ctx)

        self.assertEqual(
            tuple(result.outcome for result in results),
            (
                mounts.MOUNT_ALREADY_PRESENT,
                mounts.MOUNT_CREATED,
                mounts.MOUNT_FAILED,
            ),
        )
        self.assertEqual(
            tuple(result.owned for result in results),
            (False, True, False),
        )
        self.assertEqual(
            tuple(result.created_directory for result in results),
            (False, True, True),
        )
        self.assertIsInstance(
            results[2].error,
            Exception,
        )
        self.assertEqual(len(commands), 2)
        self.assertTrue(proc.is_dir())
        self.assertTrue(sys.is_dir())

    def test_mount_dbus_reports_the_symlinked_run_layout(self):
        (self.fs_dir / "var").mkdir()
        (self.fs_dir / "run").mkdir()
        os.symlink("../run", str(self.fs_dir / "var/run"))
        mounted = set()

        def fake_run_ok(command):
            mounted.add(command[-1])
            return True

        with mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ), mock.patch.object(
            mounts,
            "run_ok",
            side_effect=fake_run_ok,
        ):
            results = mounts.mount_dbus(self.ctx)

        self.assertEqual(
            tuple(result.source for result in results),
            ("/var/lib/dbus", "/run/dbus"),
        )
        self.assertEqual(
            tuple(result.destination for result in results),
            (
                str(self.fs_dir / "var/lib/dbus"),
                str(self.fs_dir / "run/dbus"),
            ),
        )
        self.assertTrue(all(result.owned for result in results))

    def test_recursive_backstop_is_deepest_first_and_preserves_trees(
        self,
    ):
        dev = str(self.fs_dir / "dev")
        candidates = (
            dev,
            dev + "/pts",
            str(self.fs_dir / "proc"),
            str(self.fs_dir / "proc/deeper"),
        )
        unmounted = []

        def fake_unmount(destination, label, extra_error=False):
            unmounted.append((destination, label, extra_error))
            return mounts.UnmountResult(
                destination=destination,
                label=label,
                outcome=mounts.UNMOUNTED,
            )

        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=candidates,
        ), mock.patch.object(
            mounts,
            "_umount_one",
            side_effect=fake_unmount,
        ):
            results = mounts.recursive_umount(
                self.ctx,
                preserve=(dev,),
            )

        self.assertEqual(
            tuple(result.destination for result in results),
            (
                str(self.fs_dir / "proc/deeper"),
                str(self.fs_dir / "proc"),
            ),
        )
        self.assertEqual(
            tuple(item[0] for item in unmounted),
            (
                str(self.fs_dir / "proc/deeper"),
                str(self.fs_dir / "proc"),
            ),
        )

    def test_session_releases_only_owned_mounts_in_reverse_order(self):
        dev = str(self.fs_dir / "dev")
        proc = str(self.fs_dir / "proc")
        sys = str(self.fs_dir / "sys")
        mounted = {dev, proc, sys}
        acquisitions = (
            self._acquisition(
                "/dev",
                dev,
                "/dev",
                mounts.MOUNT_CREATED,
                owned=True,
            ),
            self._acquisition(
                "/proc",
                proc,
                "/proc",
                mounts.MOUNT_ALREADY_PRESENT,
            ),
            self._acquisition(
                "/sys",
                sys,
                "/sys",
                mounts.MOUNT_CREATED,
                owned=True,
            ),
        )
        direct_order = []

        def fake_unmount(destination, label, extra_error=False):
            direct_order.append(destination)
            mounted.discard(destination)
            return mounts.UnmountResult(
                destination=destination,
                label=label,
                outcome=mounts.UNMOUNTED,
            )

        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=(proc,),
        ), mock.patch.object(
            mounts,
            "mount_sys",
            return_value=acquisitions,
        ), mock.patch.object(
            mounts,
            "_umount_one",
            side_effect=fake_unmount,
        ), mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=tuple(),
        ) as backstop, mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ):
            with MountSession(self.ctx) as session:
                session.mount_sys()

        self.assertEqual(direct_order, [sys, dev])
        self.assertNotIn(proc, direct_order)
        self.assertEqual(
            set(backstop.call_args.kwargs["preserve"]),
            {proc},
        )

    def test_partial_mount_failure_cleans_owned_mounts_and_roots(self):
        dev = self.fs_dir / "dev"
        proc = self.fs_dir / "proc"
        dev.mkdir()
        proc.mkdir()
        mounted = {str(dev)}
        acquisitions = (
            self._acquisition(
                "/dev",
                dev,
                "/dev",
                mounts.MOUNT_CREATED,
                owned=True,
                created_directory=True,
            ),
            self._acquisition(
                "/proc",
                proc,
                "/proc",
                mounts.MOUNT_FAILED,
                created_directory=True,
                error=RuntimeError("simulated mount failure"),
            ),
        )
        unmounted = []

        def fake_unmount(destination, label, extra_error=False):
            unmounted.append(destination)
            mounted.discard(destination)
            return mounts.UnmountResult(
                destination=destination,
                label=label,
                outcome=mounts.UNMOUNTED,
            )

        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "mount_sys",
            return_value=acquisitions,
        ), mock.patch.object(
            mounts,
            "_umount_one",
            side_effect=fake_unmount,
        ), mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ):
            with self.assertRaises(MountAcquisitionError) as captured:
                with MountSession(self.ctx) as session:
                    session.mount_sys()

        self.assertEqual(len(captured.exception.results), 1)
        self.assertEqual(unmounted, [str(dev)])
        self.assertFalse(os.path.lexists(str(dev)))
        self.assertFalse(os.path.lexists(str(proc)))
        self.assertEqual(mounted, set())

    def test_x_grant_exception_still_invokes_revocation(self):
        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "allow_local_x_access",
            side_effect=RuntimeError("simulated xhost failure"),
        ), mock.patch.object(
            mounts,
            "block_local_x_access",
            return_value=True,
        ) as revoke, mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=tuple(),
        ):
            with self.assertRaises(MountAcquisitionError):
                with MountSession(self.ctx) as session:
                    session.allow_local_x_access()

        revoke.assert_called_once_with()

    def test_cleanup_attempts_every_step_and_chains_primary(self):
        dev = str(self.fs_dir / "dev")
        sys = str(self.fs_dir / "sys")
        mounted = {dev, sys}
        acquisitions = (
            self._acquisition(
                "/dev",
                dev,
                "/dev",
                mounts.MOUNT_CREATED,
                owned=True,
            ),
            self._acquisition(
                "/sys",
                sys,
                "/sys",
                mounts.MOUNT_CREATED,
                owned=True,
            ),
        )
        direct_order = []

        def fail_unmount(destination, label, extra_error=False):
            direct_order.append(destination)
            return mounts.UnmountResult(
                destination=destination,
                label=label,
                outcome=mounts.UNMOUNT_FAILED,
                error=RuntimeError(
                    f"simulated direct failure: {destination}"
                ),
            )

        primary_error = RuntimeError("simulated caller failure")
        backstop_failure = mounts.UnmountResult(
            destination=str(self.fs_dir / "nested"),
            label="/nested",
            outcome=mounts.UNMOUNT_FAILED,
            error=RuntimeError("simulated backstop failure"),
        )

        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "mount_sys",
            return_value=acquisitions,
        ), mock.patch.object(
            mounts,
            "_umount_one",
            side_effect=fail_unmount,
        ), mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=(backstop_failure,),
        ), mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ), mock.patch.object(
            mounts,
            "allow_local_x_access",
            return_value=True,
        ), mock.patch.object(
            mounts,
            "block_local_x_access",
            return_value=False,
        ) as revoke:
            with self.assertRaises(
                MountSessionCleanupError
            ) as captured:
                with MountSession(self.ctx) as session:
                    session.mount_sys()
                    session.allow_local_x_access()
                    raise primary_error

        self.assertIs(captured.exception.__cause__, primary_error)
        self.assertEqual(direct_order, [sys, dev])
        self.assertEqual(
            tuple(
                failure.operation
                for failure in captured.exception.failures
            ),
            (
                "unmount_owned",
                "unmount_owned",
                "recursive_unmount_backstop",
                "revoke_local_x_access",
            ),
        )
        revoke.assert_called_once_with()

    def test_cleanup_failures_remain_retryable_and_idempotent(self):
        dev = self.fs_dir / "dev"
        dev.mkdir()
        mounted = {str(dev)}
        acquisition = self._acquisition(
            "/dev",
            dev,
            "/dev",
            mounts.MOUNT_CREATED,
            owned=True,
            created_directory=True,
        )
        attempt = {"number": 1}

        def retrying_unmount(destination, label, extra_error=False):
            if attempt["number"] == 1:
                return mounts.UnmountResult(
                    destination=destination,
                    label=label,
                    outcome=mounts.UNMOUNT_FAILED,
                    error=RuntimeError("first unmount failed"),
                )
            mounted.discard(destination)
            return mounts.UnmountResult(
                destination=destination,
                label=label,
                outcome=mounts.UNMOUNTED,
            )

        def retrying_backstop(ctx, preserve=()):
            if attempt["number"] == 1:
                return (
                    mounts.UnmountResult(
                        destination=str(dev),
                        label="/dev",
                        outcome=mounts.UNMOUNT_FAILED,
                        error=RuntimeError("first backstop failed"),
                    ),
                )
            return tuple()

        def retrying_revoke():
            return attempt["number"] != 1

        session = MountSession(self.ctx)
        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "mount_sys",
            return_value=(acquisition,),
        ), mock.patch.object(
            mounts,
            "_umount_one",
            side_effect=retrying_unmount,
        ), mock.patch.object(
            mounts,
            "recursive_umount",
            side_effect=retrying_backstop,
        ), mock.patch.object(
            mounts,
            "is_mounted",
            side_effect=lambda path: str(path) in mounted,
        ), mock.patch.object(
            mounts,
            "allow_local_x_access",
            return_value=True,
        ), mock.patch.object(
            mounts,
            "block_local_x_access",
            side_effect=retrying_revoke,
        ):
            session.__enter__()
            session.mount_sys()
            session.allow_local_x_access()
            with self.assertRaises(MountSessionCleanupError):
                session.cleanup()

            self.assertTrue(dev.is_dir())
            attempt["number"] = 2
            session.cleanup()
            session.cleanup()

        self.assertFalse(os.path.lexists(str(dev)))
        self.assertEqual(mounted, set())

    def test_created_directory_cleanup_failure_chains_and_retries(self):
        failed_root = self.fs_dir / "proc"
        failed_root.mkdir()
        acquisition = self._acquisition(
            "/proc",
            failed_root,
            "/proc",
            mounts.MOUNT_FAILED,
            created_directory=True,
            error=RuntimeError("simulated acquisition failure"),
        )
        real_rmdir = mount_session.os.rmdir
        failed_once = False

        def fail_rmdir_once(path):
            nonlocal failed_once
            if not failed_once and str(path) == str(failed_root):
                failed_once = True
                raise OSError("simulated root removal failure")
            return real_rmdir(path)

        session = MountSession(self.ctx)
        with mock.patch.object(
            mounts,
            "mounted_paths",
            return_value=tuple(),
        ), mock.patch.object(
            mounts,
            "mount_sys",
            return_value=(acquisition,),
        ), mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=tuple(),
        ), mock.patch.object(
            mount_session.os,
            "rmdir",
            side_effect=fail_rmdir_once,
        ):
            with self.assertRaises(
                MountSessionCleanupError
            ) as captured:
                with session:
                    session.mount_sys()

        self.assertIsInstance(
            captured.exception.__cause__,
            MountAcquisitionError,
        )
        self.assertEqual(len(captured.exception.failures), 1)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "remove_mount_directory",
        )
        self.assertTrue(failed_root.is_dir())

        with mock.patch.object(
            mounts,
            "recursive_umount",
            return_value=tuple(),
        ):
            session.cleanup()
        self.assertFalse(os.path.lexists(str(failed_root)))

    def test_entry_fails_closed_when_mount_inventory_is_unavailable(self):
        with mock.patch.object(
            mounts,
            "mounted_paths",
            side_effect=OSError("simulated mount-table read failure"),
        ), mock.patch.object(
            mounts,
            "recursive_umount",
        ) as backstop:
            with self.assertRaisesRegex(
                MountAcquisitionError,
                "pre-existing",
            ):
                with MountSession(self.ctx):
                    self.fail("Unavailable mount evidence must reject entry")

        backstop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
