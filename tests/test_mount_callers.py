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
from liveusb.backend.mount_session import MountSession

from tests.test_mount_session import FakeMountTable
from tests.test_mount_session import FakeXAccess


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

    def stage_file(
        self,
        source,
        purpose,
        suffix="",
        executable=False,
    ):
        self.events.append(
            (
                "stage_file",
                source,
                purpose,
                suffix,
                executable,
            )
        )
        return f"/tmp/staged-{purpose}{suffix}"


class _RecordingProcess:
    def __init__(self, events):
        self.events = events

    def terminate(self):
        self.events.append("terminate")

    def poll(self):
        return None

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        return 0

    def kill(self):
        self.events.append("kill")


class _ScriptedProcess:
    def __init__(
        self,
        poll_result=None,
        poll_error=None,
        terminate_error=None,
        wait_results=(),
        kill_error=None,
    ):
        self.poll_result = poll_result
        self.poll_error = poll_error
        self.terminate_error = terminate_error
        self.wait_results = list(wait_results)
        self.kill_error = kill_error
        self.events = []

    def poll(self):
        self.events.append("poll")
        if self.poll_error is not None:
            raise self.poll_error
        return self.poll_result

    def terminate(self):
        self.events.append("terminate")
        if self.terminate_error is not None:
            raise self.terminate_error

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        if not self.wait_results:
            return 0
        result = self.wait_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def kill(self):
        self.events.append("kill")
        if self.kill_error is not None:
            raise self.kill_error


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
                (
                    "stage_file",
                    str(package),
                    "deb",
                    ".deb",
                    False,
                ),
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
                (
                    "stage_file",
                    str(hook_file),
                    "hook",
                    "",
                    True,
                ),
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
        process = _RecordingProcess(session_events)

        def launch_process(*_args, **_kwargs):
            session_events.append("popen")
            return process

        with self._simulated_chroot_failure(
            xnest,
            session_events,
        ) as (_chroot_run, session_factory), mock.patch.object(
            xnest.subprocess,
            "Popen",
            side_effect=launch_process,
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
                "popen",
                "allow_x",
                "mount_sys",
                "mount_dbus",
                "terminate",
                ("wait", 5),
                ("exit", "RuntimeError"),
            ],
        )
        session_factory.assert_called_once_with(self.context)
        popen.assert_called_once()

    def test_xnest_cleanup_failure_chains_mount_or_operation_error(self):
        session_directory = (
            self.work_dir / "FileSystem/usr/share/xsessions"
        )
        session_directory.mkdir(parents=True)
        (session_directory / "ubuntu.desktop").write_text(
            "[Desktop Entry]\nExec=test-session\n",
            encoding="utf-8",
        )
        session_events = []
        stop_failure = OSError("synthetic terminate failure")
        process = _ScriptedProcess(
            terminate_error=stop_failure,
            wait_results=(0,),
        )

        with self._simulated_chroot_failure(
            xnest,
            session_events,
        ), mock.patch.object(
            xnest.subprocess,
            "Popen",
            return_value=process,
        ), mock.patch.object(
            xnest.time,
            "sleep",
        ):
            with self.assertRaises(
                xnest.XephyrCleanupError
            ) as captured:
                xnest.run_xnest(self.context)

        self.assertIsInstance(
            captured.exception.__cause__,
            RuntimeError,
        )
        self.assertEqual(
            captured.exception.failures[0].operation,
            "terminate",
        )
        self.assertIs(
            captured.exception.failures[0].error,
            stop_failure,
        )

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


class CallerStagingIntegrationTests(unittest.TestCase):
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
        self.context = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(self.root / "runtime"),
        )
        self.table = FakeMountTable()
        self.x_access = FakeXAccess(
            enabled=False,
            local_present=False,
        )

    def session_factory(self, ctx):
        return MountSession(
            ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

    @contextlib.contextmanager
    def patched_caller(self, module, chroot_effect=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(module.mounts, "check_fs_dir")
            )
            stack.enter_context(
                mock.patch.object(module.mounts, "check_lock")
            )
            stack.enter_context(
                mock.patch.object(module.chroot, "check_sources_list")
            )
            session_factory = stack.enter_context(
                mock.patch.object(
                    module.mount_session,
                    "MountSession",
                    side_effect=self.session_factory,
                )
            )
            chroot_run = stack.enter_context(
                mock.patch.object(
                    module.chroot,
                    "chroot_run",
                    side_effect=chroot_effect,
                )
            )
            yield chroot_run, session_factory

    def test_debian_staging_is_unique_and_residue_free(self):
        package = self.root / "sample.deb"
        package.write_bytes(b"synthetic deb")
        self.context.deb = str(package)
        legacy = self.fs_dir / "tmp/temp.deb"
        legacy.write_bytes(b"legacy")

        with self.patched_caller(deb) as (
            chroot_run,
            session_factory,
        ):
            deb.run_deb(self.context)

        staged_argument = chroot_run.call_args_list[0].args[3]
        self.assertTrue(staged_argument.startswith("/tmp/liveusb-deb-"))
        self.assertTrue(staged_argument.endswith(".deb"))
        self.assertEqual(legacy.read_bytes(), b"legacy")
        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        session_factory.assert_called_once_with(self.context)

    def test_hook_staging_is_unique_and_residue_free_after_failure(self):
        hook_file = self.root / "hook.sh"
        hook_file.write_text(
            "#!/bin/sh\nexit 0\n",
            encoding="utf-8",
        )
        self.context.hook = str(hook_file)
        legacy = self.fs_dir / "tmp/HOOK"
        legacy.write_bytes(b"legacy")

        with self.patched_caller(
            hook,
            chroot_effect=RuntimeError("synthetic hook failure"),
        ) as (chroot_run, session_factory):
            with self.assertRaisesRegex(
                RuntimeError,
                "synthetic hook failure",
            ):
                hook.run_hook(self.context)

        staged_argument = chroot_run.call_args.args[1]
        self.assertTrue(staged_argument.startswith("/tmp/liveusb-hook-"))
        self.assertEqual(legacy.read_bytes(), b"legacy")
        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        session_factory.assert_called_once_with(self.context)


class XephyrLifecycleTests(unittest.TestCase):
    def test_already_exited_process_is_distinguished(self):
        process = _ScriptedProcess(poll_result=0)

        result = xnest._stop_xephyr(process)

        self.assertEqual(result, "already-exited")
        self.assertEqual(process.events, ["poll"])

    def test_terminate_and_wait_prove_exit(self):
        process = _ScriptedProcess(wait_results=(0,))

        result = xnest._stop_xephyr(process)

        self.assertEqual(result, "terminated")
        self.assertEqual(
            process.events,
            ["poll", "terminate", ("wait", 5)],
        )

    def test_wait_timeout_invokes_kill_and_second_wait(self):
        process = _ScriptedProcess(
            wait_results=(
                subprocess.TimeoutExpired("Xephyr", 5),
                0,
            )
        )

        result = xnest._stop_xephyr(process)

        self.assertEqual(result, "killed")
        self.assertEqual(
            process.events,
            [
                "poll",
                "terminate",
                ("wait", 5),
                "kill",
                ("wait", 5),
            ],
        )

    def test_terminate_failure_is_surfaced_after_exit_is_proven(self):
        failure = OSError("synthetic terminate failure")
        process = _ScriptedProcess(
            terminate_error=failure,
            wait_results=(0,),
        )

        with self.assertRaises(xnest.XephyrCleanupError) as captured:
            xnest._stop_xephyr(process)

        self.assertIs(captured.exception.failures[0].error, failure)
        self.assertEqual(
            captured.exception.failures[0].operation,
            "terminate",
        )
        self.assertEqual(
            process.events,
            ["poll", "terminate", ("wait", 5)],
        )

    def test_wait_failure_still_attempts_kill_and_final_wait(self):
        failure = OSError("synthetic wait failure")
        process = _ScriptedProcess(
            wait_results=(failure, 0),
        )

        with self.assertRaises(xnest.XephyrCleanupError) as captured:
            xnest._stop_xephyr(process)

        self.assertEqual(
            tuple(
                item.operation
                for item in captured.exception.failures
            ),
            ("wait-after-terminate",),
        )
        self.assertEqual(
            process.events,
            [
                "poll",
                "terminate",
                ("wait", 5),
                "kill",
                ("wait", 5),
            ],
        )

    def test_kill_failure_is_surfaced_after_final_wait(self):
        failure = OSError("synthetic kill failure")
        process = _ScriptedProcess(
            wait_results=(
                subprocess.TimeoutExpired("Xephyr", 5),
                0,
            ),
            kill_error=failure,
        )

        with self.assertRaises(xnest.XephyrCleanupError) as captured:
            xnest._stop_xephyr(process)

        self.assertEqual(
            tuple(
                item.operation
                for item in captured.exception.failures
            ),
            ("kill",),
        )
        self.assertIs(captured.exception.failures[0].error, failure)
        self.assertEqual(
            process.events,
            [
                "poll",
                "terminate",
                ("wait", 5),
                "kill",
                ("wait", 5),
            ],
        )


if __name__ == "__main__":
    unittest.main()
