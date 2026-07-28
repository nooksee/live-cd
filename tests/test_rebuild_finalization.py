from __future__ import annotations

import hashlib
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb import messages
from liveusb.backend import Context
from liveusb.backend import rebuild


class RebuildFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work = self.root / "work"
        self.iso_tree = self.work / "ISO"
        self.fs_tree = self.work / "FileSystem"
        for path in (
            self.iso_tree / "isolinux",
            self.iso_tree / "casper",
            self.iso_tree / ".disk",
            self.fs_tree,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.iso_tree / "isolinux/isolinux.bin").write_bytes(
            b"legacy boot image"
        )
        self.ctx = Context(
            work_dir=str(self.work),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(self.root / "runtime"),
            compression="xz",
        )
        self.iso_path = self.work / "Ubuntu-amd64-14.04.iso"
        self.sidecar_path = self.work / "Ubuntu-amd64-14.04.sha256"

    @staticmethod
    def result(returncode=0):
        return types.SimpleNamespace(returncode=returncode)

    def test_compression_capability_is_decided_before_one_build(self):
        events = []

        def probe(command, **_kwargs):
            events.append(("probe", tuple(command)))
            return self.result(0)

        def runner(command):
            events.append(("build", tuple(command)))
            return self.result(0)

        command = rebuild._build_squashfs(
            self.ctx,
            str(self.iso_tree / "casper/filesystem.squashfs"),
            probe=probe,
            runner=runner,
        )

        self.assertEqual([event[0] for event in events], ["probe", "build"])
        self.assertEqual(len([event for event in events if event[0] == "build"]), 1)
        self.assertEqual(command[-2:], ["-comp", "xz"])

    def test_unsupported_compression_uses_one_default_build(self):
        commands = []

        command = rebuild._build_squashfs(
            self.ctx,
            str(self.iso_tree / "casper/filesystem.squashfs"),
            probe=lambda _command, **_kwargs: self.result(1),
            runner=lambda value: commands.append(value) or self.result(0),
        )

        self.assertNotIn("-comp", command)
        self.assertEqual(commands, [command])

    def test_squashfs_failure_is_not_retried(self):
        commands = []

        with self.assertRaises(messages.LiveUSBError):
            rebuild._build_squashfs(
                self.ctx,
                str(self.iso_tree / "casper/filesystem.squashfs"),
                probe=lambda _command, **_kwargs: self.result(0),
                runner=lambda value: commands.append(value) or self.result(1),
            )

        self.assertEqual(len(commands), 1)

    def test_compression_probe_execution_failure_is_liveusb_error(self):
        def fail_probe(_command, **_kwargs):
            raise OSError("synthetic probe failure")

        with self.assertRaises(messages.LiveUSBError):
            rebuild._plan_mksquashfs_command(
                self.ctx,
                str(self.iso_tree / "casper/filesystem.squashfs"),
                probe=fail_probe,
            )

    def test_stale_iso_and_sidecar_cleanup_is_bounded(self):
        self.iso_path.write_bytes(b"stale ISO")
        self.sidecar_path.write_text("stale hash\n", encoding="utf-8")
        temporary = Path(
            rebuild._sha256_temporary_path(str(self.sidecar_path))
        )
        temporary.write_text("partial hash\n", encoding="utf-8")
        unrelated = self.work / "unrelated.txt"
        unrelated.write_text("preserve\n", encoding="utf-8")

        rebuild._remove_stale_outputs(
            (
                str(self.iso_path),
                str(self.sidecar_path),
                str(temporary),
            )
        )

        self.assertFalse(self.iso_path.exists())
        self.assertFalse(self.sidecar_path.exists())
        self.assertFalse(temporary.exists())
        self.assertTrue(unrelated.exists())

    def test_stale_output_cleanup_failure_is_liveusb_error(self):
        self.iso_path.mkdir()

        with self.assertRaises(messages.LiveUSBError):
            rebuild._remove_stale_outputs((str(self.iso_path),))

    def test_finalization_mutates_seals_then_hashes_final_bytes(self):
        self.iso_path.write_bytes(b"generated ISO")
        events = []

        def mutate(command):
            events.append(("mutate", tuple(command)))
            with self.iso_path.open("ab") as handle:
                handle.write(b" + hybrid")
            return self.result(0)

        digest = rebuild._finalize_legacy_iso(
            self.ctx,
            str(self.iso_path),
            str(self.sidecar_path),
            runner=mutate,
        )

        final_bytes = self.iso_path.read_bytes()
        self.assertEqual(digest, hashlib.sha256(final_bytes).hexdigest())
        self.assertEqual(os.stat(self.iso_path).st_mode & 0o777, 0o555)
        self.assertEqual(events[0][0], "mutate")
        self.assertEqual(
            self.sidecar_path.read_text(encoding="utf-8"),
            f"{digest}  {self.iso_path}\n",
        )

    def test_nonlegacy_profile_runs_isohybrid_zero_times(self):
        (self.iso_tree / "isolinux/isolinux.bin").unlink()
        self.iso_path.write_bytes(b"generated ISO")
        runner = mock.Mock(return_value=self.result(0))

        with self.assertRaises(messages.LiveUSBError):
            rebuild._finalize_legacy_iso(
                self.ctx,
                str(self.iso_path),
                str(self.sidecar_path),
                runner=runner,
            )

        runner.assert_not_called()
        self.assertFalse(self.sidecar_path.exists())

    def test_isohybrid_failure_prevents_sealing_and_sidecar(self):
        self.iso_path.write_bytes(b"generated ISO")
        self.iso_path.chmod(0o644)

        with self.assertRaises(messages.LiveUSBError):
            rebuild._finalize_legacy_iso(
                self.ctx,
                str(self.iso_path),
                str(self.sidecar_path),
                runner=lambda _command: self.result(1),
            )

        self.assertEqual(os.stat(self.iso_path).st_mode & 0o777, 0o644)
        self.assertFalse(self.sidecar_path.exists())

    def test_sealing_failure_prevents_sidecar_publication(self):
        self.iso_path.write_bytes(b"generated ISO")

        with mock.patch.object(
            rebuild.os,
            "chmod",
            side_effect=OSError("synthetic chmod failure"),
        ):
            with self.assertRaises(messages.LiveUSBError):
                rebuild._finalize_legacy_iso(
                    self.ctx,
                    str(self.iso_path),
                    str(self.sidecar_path),
                    runner=lambda _command: self.result(0),
                )

        self.assertFalse(self.sidecar_path.exists())

    def test_sidecar_replace_failure_publishes_no_partial_evidence(self):
        self.iso_path.write_bytes(b"final ISO bytes")

        with mock.patch.object(
            rebuild.os,
            "replace",
            side_effect=OSError("synthetic publication failure"),
        ):
            with self.assertRaises(messages.LiveUSBError):
                rebuild._publish_sha256(
                    str(self.iso_path),
                    str(self.sidecar_path),
                )

        self.assertFalse(self.sidecar_path.exists())
        self.assertFalse(
            Path(
                rebuild._sha256_temporary_path(
                    str(self.sidecar_path)
                )
            ).exists()
        )

    def test_missing_final_iso_hash_failure_is_liveusb_error(self):
        with self.assertRaises(messages.LiveUSBError):
            rebuild._publish_sha256(
                str(self.iso_path),
                str(self.sidecar_path),
            )

        self.assertFalse(self.sidecar_path.exists())

    def test_run_rebuild_uses_one_planned_build_and_final_order(self):
        (self.fs_tree / "etc").mkdir()
        (self.fs_tree / "boot").mkdir()
        (self.fs_tree / "etc/lsb-release").write_text(
            "DISTRIB_ID=Ubuntu\n"
            "DISTRIB_RELEASE=14.04\n"
            "DISTRIB_CODENAME=trusty\n",
            encoding="utf-8",
        )
        (self.fs_tree / "etc/casper.conf").write_text(
            'export USERNAME="ubuntu"\n',
            encoding="utf-8",
        )
        (self.fs_tree / "boot/initrd.img-1").write_bytes(b"initrd")
        (self.fs_tree / "boot/vmlinuz-1").write_bytes(b"kernel")
        events = []

        class Session:
            def __enter__(self):
                events.append("session-enter")
                return self

            def mount_sys(self):
                events.append("mount-sys")

            def __exit__(self, exc_type, _exc, _traceback):
                events.append(("session-exit", exc_type))

        def command_runner(command, **_kwargs):
            executable = command[0]
            events.append(executable)
            if executable == "mksquashfs":
                Path(command[2]).write_bytes(b"squashfs")
            elif executable == "genisoimage":
                Path(command[command.index("-o") + 1]).write_bytes(
                    b"generated ISO"
                )
            elif executable == "isohybrid":
                with Path(command[1]).open("ab") as handle:
                    handle.write(b" + hybrid")
            return self.result(0)

        def process_runner(command, **_kwargs):
            if command[-1] == "--print-architecture":
                return types.SimpleNamespace(
                    returncode=0,
                    stdout="amd64\n",
                )
            return types.SimpleNamespace(
                returncode=0,
                stdout="base-files 1\n",
            )

        with mock.patch.object(
            rebuild.mounts,
            "check_fs_dir",
        ), mock.patch.object(
            rebuild.mounts,
            "check_lock",
        ), mock.patch.object(
            rebuild.chroot,
            "update_distro_name",
        ), mock.patch.object(
            rebuild.chroot,
            "check_sources_list",
        ), mock.patch.object(
            rebuild.chroot,
            "chroot_run",
        ), mock.patch.object(
            rebuild.mount_session,
            "MountSession",
            return_value=Session(),
        ), mock.patch.object(
            rebuild,
            "_compression_is_supported",
            return_value=True,
        ), mock.patch.object(
            rebuild,
            "run",
            side_effect=command_runner,
        ), mock.patch.object(
            rebuild.subprocess,
            "run",
            side_effect=process_runner,
        ), mock.patch.object(
            rebuild.shutil,
            "which",
            return_value=None,
        ):
            rebuild.run_rebuild(self.ctx)

        self.assertEqual(events.count("mksquashfs"), 1)
        self.assertLess(events.index("mksquashfs"), events.index("genisoimage"))
        self.assertLess(events.index("genisoimage"), events.index("isohybrid"))
        output = self.work / "Ubuntu-amd64-14.04.iso"
        sidecar = self.work / "Ubuntu-amd64-14.04.sha256"
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o555)
        self.assertEqual(
            sidecar.read_text(encoding="utf-8"),
            f"{digest}  {output}\n",
        )


if __name__ == "__main__":
    unittest.main()
