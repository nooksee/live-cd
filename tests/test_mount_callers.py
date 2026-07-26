from __future__ import annotations

import contextlib
import inspect
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import chroot_shell
from liveusb.backend import deb
from liveusb.backend import gui_install
from liveusb.backend import hook
from liveusb.backend import pkgm
from liveusb.backend import rebuild
from liveusb.backend import xnest


class _RecordingSession:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, exception_type, _error, _traceback):
        if exception_type is None:
            exception_name = None
        else:
            exception_name = exception_type.__name__
        self.events.append(("exit", exception_name))
        return False

    def allow_local_x_access(self):
        self.events.append("allow_x")

    def mount_sys(self):
        self.events.append("mount_sys")

    def mount_dbus(self):
        self.events.append("mount_dbus")


class _RecordingProcess:
    def __init__(self, events):
        self.events = events

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        return 0

    def kill(self):
        self.events.append("kill")


class MountCallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.work_dir = root / "work"
        self.mount_dir = root / "mount"
        self.context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.mount_dir),
        )
        for relative in (
            "FileSystem/etc",
            "FileSystem/usr",
            "FileSystem/root",
            "FileSystem/tmp",
            "ISO",
        ):
            (self.work_dir / relative).mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _simulated_chroot_failure(self, module, events):
        session = _RecordingSession(events)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(module.mounts, "check_fs_dir")
            )
            stack.enter_context(
                mock.patch.object(module.mounts, "check_lock")
            )
            stack.enter_context(
                mock.patch.object(module.chroot, "update_distro_name")
            )
            stack.enter_context(
                mock.patch.object(module.chroot, "check_sources_list")
            )
            chroot_run = stack.enter_context(
                mock.patch.object(
                    module.chroot,
                    "chroot_run",
                    side_effect=RuntimeError(
                        "simulated chroot command failure"
                    ),
                )
            )
            session_factory = stack.enter_context(
                mock.patch.object(
                    module.mount_session,
                    "MountSession",
                    return_value=session,
                )
            )
            yield chroot_run, session_factory

    def test_chroot_shell_releases_session_after_command_failure(self):
        events = []

        with self._simulated_chroot_failure(
            chroot_shell,
            events,
        ) as (_chroot_run, session_factory):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                chroot_shell.run_chroot(self.context)

        self.assertEqual(
            events,
            ["enter", "mount_sys", ("exit", "RuntimeError")],
        )
        session_factory.assert_called_once_with(self.context)

    def test_deb_releases_x_and_mount_session_after_command_failure(self):
        package = Path(self.temporary_directory.name) / "sample.deb"
        package.write_bytes(b"synthetic package")
        self.context.deb = str(package)
        events = []

        with self._simulated_chroot_failure(
            deb,
            events,
        ) as (_chroot_run, session_factory):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                deb.run_deb(self.context)

        self.assertEqual(
            events,
            [
                "enter",
                "allow_x",
                "mount_sys",
                ("exit", "RuntimeError"),
            ],
        )
        session_factory.assert_called_once_with(self.context)

    def test_gui_install_releases_session_after_command_failure(self):
        events = []

        with self._simulated_chroot_failure(
            gui_install,
            events,
        ) as (_chroot_run, session_factory):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                gui_install.run_gui_install(self.context, choice="1")

        self.assertEqual(
            events,
            [
                "enter",
                "mount_sys",
                "mount_dbus",
                ("exit", "RuntimeError"),
            ],
        )
        session_factory.assert_called_once_with(self.context)

    def test_hook_releases_x_and_mount_session_after_command_failure(self):
        hook_file = Path(self.temporary_directory.name) / "hook.sh"
        hook_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.context.hook = str(hook_file)
        events = []

        with self._simulated_chroot_failure(
            hook,
            events,
        ) as (_chroot_run, session_factory):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                hook.run_hook(self.context)

        self.assertEqual(
            events,
            [
                "enter",
                "allow_x",
                "mount_sys",
                "mount_dbus",
                ("exit", "RuntimeError"),
            ],
        )
        session_factory.assert_called_once_with(self.context)

    def test_pkgm_releases_x_and_mount_session_after_command_failure(self):
        events = []

        with self._simulated_chroot_failure(
            pkgm,
            events,
        ) as (_chroot_run, session_factory), mock.patch.object(
            pkgm,
            "_find_pkgm",
            return_value="aptitude",
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                pkgm.run_pkgm(self.context)

        self.assertEqual(
            events,
            [
                "enter",
                "allow_x",
                "mount_sys",
                "mount_dbus",
                ("exit", "RuntimeError"),
            ],
        )
        session_factory.assert_called_once_with(self.context)

    def test_rebuild_releases_session_after_command_failure(self):
        (self.work_dir / "ISO/isolinux").mkdir(
            parents=True,
            exist_ok=True,
        )
        (self.work_dir / "FileSystem/boot").mkdir(
            parents=True,
            exist_ok=True,
        )
        (self.work_dir / "FileSystem/boot/initrd.img-1").write_bytes(
            b"initrd"
        )
        (self.work_dir / "FileSystem/boot/vmlinuz-1").write_bytes(
            b"kernel"
        )
        (self.work_dir / "FileSystem/etc/lsb-release").write_text(
            "DISTRIB_ID=Ubuntu\n"
            "DISTRIB_RELEASE=26.04\n"
            "DISTRIB_CODENAME=resolute\n",
            encoding="utf-8",
        )
        (self.work_dir / "FileSystem/etc/casper.conf").write_text(
            'export USERNAME="ubuntu"\n',
            encoding="utf-8",
        )
        events = []
        architecture = SimpleNamespace(
            returncode=0,
            stdout="amd64\n",
        )

        with self._simulated_chroot_failure(
            rebuild,
            events,
        ) as (_chroot_run, session_factory), mock.patch.object(
            rebuild.subprocess,
            "run",
            return_value=architecture,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                rebuild.run_rebuild(self.context)

        self.assertEqual(
            events,
            ["enter", "mount_sys", ("exit", "RuntimeError")],
        )
        session_factory.assert_called_once_with(self.context)

    def test_xnest_releases_session_and_process_after_command_failure(self):
        session_directory = (
            self.work_dir / "FileSystem/usr/share/xsessions"
        )
        session_directory.mkdir(parents=True)
        (session_directory / "ubuntu.desktop").write_text(
            "[Desktop Entry]\nExec=test-session\n",
            encoding="utf-8",
        )
        session_events = []
        process_events = []
        process = _RecordingProcess(process_events)

        with self._simulated_chroot_failure(
            xnest,
            session_events,
        ) as (_chroot_run, session_factory), mock.patch.object(
            xnest.subprocess,
            "Popen",
            return_value=process,
        ) as popen, mock.patch.object(
            xnest.time,
            "sleep",
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated chroot command failure",
            ):
                xnest.run_xnest(self.context)

        self.assertEqual(
            session_events,
            [
                "enter",
                "allow_x",
                "mount_sys",
                "mount_dbus",
                ("exit", "RuntimeError"),
            ],
        )
        self.assertEqual(
            process_events,
            ["terminate", ("wait", 5)],
        )
        session_factory.assert_called_once_with(self.context)
        popen.assert_called_once()

    def test_all_seven_callers_use_the_session_boundary(self):
        modules = (
            chroot_shell,
            deb,
            gui_install,
            hook,
            pkgm,
            rebuild,
            xnest,
        )
        forbidden_calls = (
            "mounts.mount_sys(",
            "mounts.umount_sys(",
            "mounts.mount_dbus(",
            "mounts.recursive_umount(",
            "mounts.allow_local_x_access(",
            "mounts.block_local_x_access(",
        )

        for module in modules:
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertIn(
                    "mount_session.MountSession(",
                    source,
                )
                for forbidden_call in forbidden_calls:
                    self.assertNotIn(forbidden_call, source)


if __name__ == "__main__":
    unittest.main()
