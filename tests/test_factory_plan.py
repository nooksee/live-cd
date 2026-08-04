from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb import constants
from liveusb.backend import Context
from liveusb.backend import extract
from liveusb.backend import factory_plan
from liveusb.backend import mounts
from liveusb.backend import preflight
from liveusb.backend import preflight_runtime
from liveusb.backend import qemu
from liveusb.backend import rebuild


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


class FactoryPlanTests(unittest.TestCase):
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
        self.ctx = Context(
            work_dir=str(self.work),
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.runtime_parent / "liveusb"),
            iso=str(self.source),
        )

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

    def preflight_engine(self, available_bytes=128 * 1024 ** 3):
        def missing_kvm(_path):
            raise FileNotFoundError("Synthetic KVM node is absent")

        return preflight.PreflightEngine(
            which=self.resolver,
            statvfs=lambda _path: self.capacity(available_bytes),
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

    def runtime_evidence(self):
        source_finding = self.preflight_engine()._inspect_source_iso(
            str(self.source)
        )
        return preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=self.runtime_executor,
        ).collect(source_finding)

    def planner(self, available_bytes=128 * 1024 ** 3):
        return factory_plan.FactoryPlanEngine(
            statvfs=lambda _path: self.capacity(available_bytes),
            preflight_engine=self.preflight_engine(available_bytes),
            expected_tool_owner_uid=os.geteuid(),
            exclude_file=str(self.exclude_file),
        )

    def make_legacy_tree(self):
        for relative in (
            "FileSystem/etc",
            "FileSystem/usr",
            "FileSystem/root",
            "FileSystem/tmp",
            "ISO/isolinux",
            "ISO/casper",
            "ISO/.disk",
        ):
            (self.work / relative).mkdir(parents=True, exist_ok=True)
        (self.work / "ISO/isolinux/isolinux.bin").write_bytes(
            b"legacy boot image"
        )

    def bindings(self, compression_supported=True):
        probe_root = self.work / ".liveusb-compression-probe-accepted"
        return factory_plan.FactoryBindings(
            mount_destination=str(
                self.mount_root / "liveusb-iso-accepted"
            ),
            probe_source=str(probe_root / "empty-source"),
            probe_output=str(probe_root / "probe.squashfs"),
            publication_candidate=str(
                self.work / ".liveusb-publish-accepted-primary"
            ),
            distribution_id="ubuntuDE",
            architecture="amd64",
            release="14.04",
            compression_supported=compression_supported,
        )

    def write_pair(self, name="ubuntuDE-amd64-14.04"):
        iso = self.work / (name + ".iso")
        sidecar = self.work / (name + ".sha256")
        iso.write_bytes(b"accepted final ISO")
        iso.chmod(0o555)
        digest = hashlib.sha256(iso.read_bytes()).hexdigest()
        sidecar.write_text(
            "{}  {}\n".format(digest, iso.name),
            encoding="ascii",
        )
        return iso, sidecar

    def test_extract_plan_is_exact_single_use_and_root_free(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        before = self.snapshot()

        with mock.patch("subprocess.Popen") as popen, mock.patch(
            "subprocess.run"
        ) as run, mock.patch("os.system") as system:
            plan = self.planner().plan(
                self.ctx,
                report,
                runtime,
                factory_plan.OPERATION_EXTRACT,
                self.bindings(),
            )

        self.assertTrue(plan.factory_authority_granted)
        self.assertEqual(plan.decision, factory_plan.DECISION_GRANTED)
        self.assertEqual(len(plan.commands), 5)
        self.assertEqual(
            tuple(command.stage for command in plan.commands),
            (
                "source-mount",
                "filesystem-extraction",
                "target-architecture-observation",
                "media-tree-copy",
                "source-unmount",
            ),
        )
        self.assertEqual(plan.commands[0].argv[1:5], (
            "-t",
            "iso9660",
            "-o",
            "ro,loop",
        ))
        self.assertEqual(plan.commands[-1].argv[-2], "-f")
        self.assertTrue(all(os.path.isabs(item.argv[0]) for item in plan.commands))
        self.assertTrue(plan.to_dict()["state_change_revokes_authority"])
        self.assertTrue(plan.to_dict()["single_use"])
        self.assertEqual(
            plan.source_artifact_id,
            "sha256:{}:size:{}".format(
                hashlib.sha256(self.source.read_bytes()).hexdigest(),
                self.source.stat().st_size,
            ),
        )
        self.assertEqual(self.snapshot(), before)
        popen.assert_not_called()
        run.assert_not_called()
        system.assert_not_called()

    def test_finalization_plan_matches_accepted_command_contract(self):
        self.make_legacy_tree()
        report = self.preflight_engine().inspect(self.ctx)
        original_exclude_file = constants.EXCLUDE_FILE
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_FINALIZE,
            self.bindings(),
        )

        self.assertTrue(plan.factory_authority_granted)
        self.assertEqual(
            tuple(command.stage for command in plan.commands),
            (
                "squashfs-capability-probe",
                "squashfs-build",
                "manifest-query",
                "iso-generation",
                "legacy-isohybrid-mutation",
            ),
        )
        build = plan.commands[1]
        self.assertEqual(build.argv[1], str(self.work / "FileSystem"))
        self.assertEqual(
            build.argv[2],
            str(self.work / "ISO/casper/filesystem.squashfs"),
        )
        self.assertEqual(build.argv[-2:], ("-comp", "xz"))
        self.assertEqual(
            build.argv[build.argv.index("-ef") + 1],
            str(self.exclude_file),
        )
        self.assertEqual(constants.EXCLUDE_FILE, original_exclude_file)
        iso = plan.commands[3]
        self.assertEqual(iso.cwd, str(self.work / "ISO"))
        self.assertEqual(
            iso.argv[iso.argv.index("-V") + 1],
            "ubuntuDE-amd64-14.04",
        )
        self.assertEqual(
            iso.argv[iso.argv.index("-o") + 1],
            self.bindings().publication_candidate,
        )
        self.assertEqual(
            plan.commands[-1].argv[-1],
            self.bindings().publication_candidate,
        )

    def test_plans_use_the_authoritative_operation_command_builders(self):
        empty_report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        bindings = self.bindings()
        extract_plan = self.planner().plan(
            self.ctx,
            empty_report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            bindings,
        )
        request = mounts.iso_mount_request(
            self.ctx,
            bindings.mount_destination,
        )
        extract_tools = {
            command.tool: command.argv[0]
            for command in extract_plan.commands
        }
        self.assertEqual(
            extract_plan.commands[0].argv,
            mounts.mount_command(
                request,
                executable=extract_tools["mount"],
            ),
        )
        self.assertEqual(
            extract_plan.commands[1].argv,
            extract.unsquashfs_command(
                self.ctx,
                bindings.mount_destination,
                executable=extract_tools["unsquashfs"],
            ),
        )
        self.assertEqual(
            extract_plan.commands[2].argv,
            extract.target_architecture_command(
                self.ctx,
                executable=extract_tools["chroot"],
            ),
        )
        self.assertEqual(
            extract_plan.commands[3].argv,
            extract.media_tree_copy_command(
                self.ctx,
                bindings.mount_destination,
                executable=extract_tools["rsync"],
            ),
        )
        self.assertEqual(
            extract_plan.commands[4].argv,
            mounts.unmount_command(
                bindings.mount_destination,
                executable=extract_tools["umount"],
                lazy=False,
            ),
        )

        self.make_legacy_tree()
        final_plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            runtime,
            factory_plan.OPERATION_FINALIZE,
            bindings,
        )
        final_tools = {
            command.tool: command.argv[0]
            for command in final_plan.commands
        }
        self.assertEqual(
            final_plan.commands[0].argv,
            rebuild.compression_probe_command(
                self.ctx.compression,
                bindings.probe_source,
                bindings.probe_output,
                executable=final_tools["mksquashfs"],
            ),
        )
        self.assertEqual(
            final_plan.commands[1].argv,
            rebuild.mksquashfs_command(
                self.ctx,
                str(self.work / "ISO/casper/filesystem.squashfs"),
                True,
                executable=final_tools["mksquashfs"],
                exclude_file=str(self.exclude_file),
            ),
        )
        self.assertEqual(
            final_plan.commands[2].argv,
            rebuild.manifest_query_command(
                self.ctx,
                executable=final_tools["chroot"],
            ),
        )
        self.assertEqual(
            final_plan.commands[3].argv,
            rebuild.genisoimage_command(
                "ubuntuDE-amd64-14.04",
                bindings.publication_candidate,
                executable=final_tools["genisoimage"],
            ),
        )
        self.assertEqual(
            final_plan.commands[4].argv,
            rebuild.isohybrid_command(
                bindings.publication_candidate,
                executable=final_tools["isohybrid"],
            ),
        )

        iso, _sidecar = self.write_pair()
        qemu_plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            runtime,
            factory_plan.OPERATION_QEMU,
            factory_plan.FactoryBindings(),
        )
        self.assertEqual(
            qemu_plan.commands[0].argv,
            qemu.qemu_command(
                qemu_plan.commands[0].argv[0],
                str(iso),
                self.ctx.vram,
            ),
        )

    def test_unsupported_compression_is_one_exact_noncompressed_build(self):
        self.make_legacy_tree()
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_FINALIZE,
            self.bindings(compression_supported=False),
        )

        self.assertTrue(plan.factory_authority_granted)
        probe, build = plan.commands[:2]
        self.assertIn("-comp", probe.argv)
        self.assertNotIn("-comp", build.argv)

    def test_qemu_plan_uses_one_valid_publication_pair(self):
        self.make_legacy_tree()
        iso, _sidecar = self.write_pair()
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_QEMU,
            factory_plan.FactoryBindings(),
        )

        self.assertTrue(plan.factory_authority_granted)
        self.assertEqual(len(plan.commands), 1)
        self.assertEqual(
            plan.commands[0].argv[1:],
            ("-cdrom", str(iso), "-m", "2048"),
        )
        self.assertEqual(plan.capacity["requirement_bytes"], 0)

    def test_fresh_preflight_refuses_publication_state_change(self):
        self.make_legacy_tree()
        _iso, sidecar = self.write_pair()
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        sidecar.unlink()

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_QEMU,
            factory_plan.FactoryBindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(plan.commands, ())
        self.assertIn(
            "finding-stale:publication.prior-pair",
            plan.reasons,
        )

    def test_missing_isohybrid_refuses_all_finalization_commands(self):
        self.make_legacy_tree()
        (self.bin_dir / "isohybrid").unlink()
        runtime = self.runtime_evidence()
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            runtime,
            factory_plan.OPERATION_FINALIZE,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(plan.commands, ())
        self.assertIsNone(plan.grant_id)
        self.assertTrue(
            any(reason.startswith("tool-isohybrid:") for reason in plan.reasons)
        )

    def test_insufficient_capacity_refuses_authority(self):
        report = self.preflight_engine().inspect(self.ctx)
        plan = self.planner(available_bytes=1024).plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(plan.commands, ())
        self.assertFalse(plan.capacity["sufficient"])
        self.assertIn("capacity-insufficient-or-unresolved", plan.reasons)

    def test_capacity_policy_is_explicit_and_conservative(self):
        report = self.preflight_engine().inspect(self.ctx)
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        source_size = self.source.stat().st_size
        expected = max(
            32 * 1024 ** 3,
            source_size * 12 + max(4 * 1024 ** 3, source_size * 2),
        )
        self.assertEqual(plan.capacity["requirement_bytes"], expected)
        self.assertFalse(plan.capacity["mathematical_upper_bound"])
        self.assertEqual(
            plan.capacity["effective_available_bytes"],
            128 * 1024 ** 3,
        )

    def test_changed_source_refuses_authority_and_preserves_evidence(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        self.source.write_bytes(b"changed after accepted evidence")
        changed = self.source.read_bytes()

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(self.source.read_bytes(), changed)
        self.assertTrue(
            any(reason.startswith("source-custody:") for reason in plan.reasons)
        )

    def test_replaced_executable_refuses_authority(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        mount_path = self.bin_dir / "mount"
        replacement = self.bin_dir / "replacement"
        replacement.write_bytes(b"different executable\n")
        replacement.chmod(0o700)
        os.replace(str(replacement), str(mount_path))

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertTrue(
            any(reason.startswith("tool-mount:") for reason in plan.reasons)
        )

    def test_symlinked_executable_path_refuses_authority(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        mount_path = self.bin_dir / "mount"
        moved_path = self.bin_dir / "mount-real"
        mount_path.rename(moved_path)
        mount_path.symlink_to(moved_path)

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertTrue(
            any(reason.startswith("tool-mount:") for reason in plan.reasons)
        )

    def test_unconfirmed_probe_termination_refuses_authority(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        changed = []
        for result in runtime.version_queries:
            if result.probe_id == "version.mount":
                evidence = dict(result.evidence)
                evidence["termination_confirmed"] = False
                result = preflight_runtime.RuntimeProbeResult(
                    result.probe_id,
                    result.status,
                    result.command,
                    evidence,
                )
            changed.append(result)
        runtime = preflight_runtime.RuntimeEvidence(
            tuple(changed),
            source_media=runtime.source_media,
        )

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertTrue(
            any(reason.startswith("tool-mount:") for reason in plan.reasons)
        )

    def test_incomplete_or_foreign_bindings_refuse_authority(self):
        self.make_legacy_tree()
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        cases = (
            factory_plan.FactoryBindings(),
            factory_plan.FactoryBindings(
                probe_source="/tmp/foreign/source",
                probe_output="/tmp/foreign/output",
                publication_candidate="/tmp/foreign/candidate",
                distribution_id="Ubuntu",
                architecture="amd64",
                release="14.04",
                compression_supported=True,
            ),
        )

        for bindings in cases:
            with self.subTest(bindings=bindings):
                plan = self.planner().plan(
                    self.ctx,
                    report,
                    runtime,
                    factory_plan.OPERATION_FINALIZE,
                    bindings,
                )
                self.assertFalse(plan.factory_authority_granted)
                self.assertEqual(plan.commands, ())

    def test_symlinked_probe_root_refuses_authority(self):
        self.make_legacy_tree()
        foreign = self.root / "foreign-probe"
        foreign.mkdir(mode=0o700)
        probe_root = self.work / ".liveusb-compression-probe-accepted"
        probe_root.symlink_to(foreign, target_is_directory=True)

        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_FINALIZE,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(plan.commands, ())
        self.assertIn("probe-root-custody-is-invalid", plan.reasons)

    def test_workspace_state_limits_authority_to_one_next_operation(self):
        empty_report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        refused_finalization = self.planner().plan(
            self.ctx,
            empty_report,
            runtime,
            factory_plan.OPERATION_FINALIZE,
            self.bindings(),
        )
        self.assertFalse(refused_finalization.factory_authority_granted)

        self.make_legacy_tree()
        extracted_report = self.preflight_engine().inspect(self.ctx)
        refused_extraction = self.planner().plan(
            self.ctx,
            extracted_report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        self.assertFalse(refused_extraction.factory_authority_granted)

    def test_fresh_preflight_refuses_workspace_change(self):
        empty_report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        self.make_legacy_tree()

        plan = self.planner().plan(
            self.ctx,
            empty_report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertEqual(plan.commands, ())
        self.assertIn("finding-stale:workspace.layout", plan.reasons)

    def test_profile_rejection_refuses_authority(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        media_evidence = dict(runtime.source_media.evidence)
        profile = dict(media_evidence["profile"])
        profile["recognized"] = False
        profile["profile"] = None
        media_evidence["profile"] = profile
        runtime = preflight_runtime.RuntimeEvidence(
            runtime.version_queries,
            source_media=preflight_runtime.RuntimeProbeResult(
                runtime.source_media.probe_id,
                preflight_runtime.STATUS_PROFILE_REJECTED,
                runtime.source_media.command,
                media_evidence,
            ),
        )

        plan = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertTrue(
            any(reason.startswith("source-custody:") for reason in plan.reasons)
        )

    def test_minimized_receipt_omits_paths_raw_output_and_descriptors(self):
        report = self.preflight_engine().inspect(self.ctx)
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        encoded = plan.receipt.to_json()
        decoded = json.loads(encoded)

        self.assertTrue(decoded["factory_authority_granted"])
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("/proc/self/fd/", encoded)
        self.assertNotIn("stdout", encoded)
        self.assertNotIn("stderr", encoded)
        self.assertNotIn("device", encoded)
        self.assertNotIn("inode", encoded)
        self.assertIn("${SOURCE_ISO}", encoded)
        self.assertIn("${TOOL:mount}", encoded)
        self.assertEqual(decoded["commands_executed"], 0)
        self.assertEqual(decoded["privileged_operations_executed"], 0)

    def test_receipt_and_plan_digests_are_stable_and_binding_sensitive(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        first = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        second = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        alternate_bindings = factory_plan.FactoryBindings(
            mount_destination=str(
                self.mount_root / "liveusb-iso-alternate"
            )
        )
        alternate = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            alternate_bindings,
        )

        self.assertEqual(first.plan_digest, second.plan_digest)
        self.assertEqual(first.evidence_digest, second.evidence_digest)
        self.assertEqual(first.grant_id, second.grant_id)
        self.assertEqual(first.receipt.to_json(), second.receipt.to_json())
        self.assertNotEqual(first.plan_digest, alternate.plan_digest)
        self.assertNotEqual(first.grant_id, alternate.grant_id)

    def test_granted_plan_and_receipt_evidence_are_deeply_immutable(self):
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        receipt_before = plan.receipt.to_json()

        with self.assertRaises(TypeError):
            plan.capacity["sufficient"] = False
        with self.assertRaises(TypeError):
            plan.tool_evidence[0]["status"] = "replaced"
        with self.assertRaises(TypeError):
            plan.receipt.payload["decision"] = "refused"

        self.assertEqual(plan.receipt.to_json(), receipt_before)

    def test_descriptor_number_does_not_change_stable_evidence_digest(self):
        report = self.preflight_engine().inspect(self.ctx)
        runtime = self.runtime_evidence()
        media_evidence = dict(runtime.source_media.evidence)
        media_evidence["source_argument"] = "/proc/self/fd/987"
        changed_media = preflight_runtime.RuntimeProbeResult(
            runtime.source_media.probe_id,
            runtime.source_media.status,
            tuple(
                "/proc/self/fd/987" if "/proc/self/fd/" in value else value
                for value in runtime.source_media.command
            ),
            media_evidence,
        )
        changed_runtime = preflight_runtime.RuntimeEvidence(
            runtime.version_queries,
            source_media=changed_media,
        )

        first = self.planner().plan(
            self.ctx,
            report,
            runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        second = self.planner().plan(
            self.ctx,
            report,
            changed_runtime,
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertEqual(first.evidence_digest, second.evidence_digest)
        self.assertEqual(first.plan_digest, second.plan_digest)

    def test_receipt_persistence_is_complete_no_clobber_and_residue_free(self):
        report = self.preflight_engine().inspect(self.ctx)
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        receipt_dir = self.root / "receipts"
        receipt_dir.mkdir(mode=0o700)
        receipt_path = receipt_dir / "phase1e-b2a.json"

        digest = factory_plan.write_receipt(
            str(receipt_path),
            plan.receipt,
            expected_owner_uid=os.geteuid(),
        )

        state = os.lstat(receipt_path)
        self.assertEqual(stat.S_IMODE(state.st_mode), 0o600)
        self.assertEqual(state.st_nlink, 1)
        self.assertEqual(receipt_path.read_bytes(), plan.receipt.to_bytes())
        self.assertEqual(digest, plan.receipt.sha256)
        self.assertEqual(list(receipt_dir.glob("*.pending-*")), [])
        with self.assertRaises(FileExistsError):
            factory_plan.write_receipt(
                str(receipt_path),
                plan.receipt,
                expected_owner_uid=os.geteuid(),
            )
        self.assertEqual(receipt_path.read_bytes(), plan.receipt.to_bytes())

    def test_receipt_persistence_rejects_aliased_or_writable_parent(self):
        report = self.preflight_engine().inspect(self.ctx)
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        actual = self.root / "actual-receipts"
        actual.mkdir(mode=0o700)
        alias = self.root / "receipt-alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            factory_plan.write_receipt(
                str(alias / "receipt.json"),
                plan.receipt,
                expected_owner_uid=os.geteuid(),
            )

        actual.chmod(0o770)
        with self.assertRaises(ValueError):
            factory_plan.write_receipt(
                str(actual / "receipt.json"),
                plan.receipt,
                expected_owner_uid=os.geteuid(),
            )

    def test_receipt_zero_progress_write_fails_without_residue(self):
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        receipt_dir = self.root / "zero-write-receipts"
        receipt_dir.mkdir(mode=0o700)
        receipt_path = receipt_dir / "receipt.json"

        with mock.patch.object(factory_plan.os, "write", return_value=0):
            with self.assertRaises(OSError):
                factory_plan.write_receipt(
                    str(receipt_path),
                    plan.receipt,
                    expected_owner_uid=os.geteuid(),
                )

        self.assertFalse(receipt_path.exists())
        self.assertEqual(list(receipt_dir.iterdir()), [])

    def test_receipt_link_race_preserves_foreign_target(self):
        plan = self.planner().plan(
            self.ctx,
            self.preflight_engine().inspect(self.ctx),
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )
        receipt_dir = self.root / "link-race-receipts"
        receipt_dir.mkdir(mode=0o700)
        receipt_path = receipt_dir / "receipt.json"
        original_link = os.link

        def race_link(source, target, **options):
            receipt_path.write_bytes(b"foreign receipt\n")
            return original_link(source, target, **options)

        with mock.patch.object(factory_plan.os, "link", side_effect=race_link):
            with self.assertRaises(FileExistsError):
                factory_plan.write_receipt(
                    str(receipt_path),
                    plan.receipt,
                    expected_owner_uid=os.geteuid(),
                )

        self.assertEqual(receipt_path.read_bytes(), b"foreign receipt\n")
        self.assertEqual(
            sorted(path.name for path in receipt_dir.iterdir()),
            ["receipt.json"],
        )

    def test_missing_required_finding_fails_closed(self):
        report = self.preflight_engine().inspect(self.ctx)
        report = preflight.PreflightReport(
            tuple(
                finding
                for finding in report.findings
                if finding.check_id != "mount.workspace"
            )
        )
        plan = self.planner().plan(
            self.ctx,
            report,
            self.runtime_evidence(),
            factory_plan.OPERATION_EXTRACT,
            self.bindings(),
        )

        self.assertFalse(plan.factory_authority_granted)
        self.assertIn("finding-missing:mount.workspace", plan.reasons)

    def snapshot(self):
        records = {}
        paths = [self.root] + sorted(
            self.root.rglob("*"),
            key=lambda path: str(path),
        )
        for path in paths:
            state = os.lstat(path)
            content = None
            if stat.S_ISREG(state.st_mode):
                content = hashlib.sha256(path.read_bytes()).hexdigest()
            records[str(path.relative_to(self.root))] = (
                state.st_dev,
                state.st_ino,
                state.st_mode,
                state.st_nlink,
                state.st_size,
                content,
            )
        return records


if __name__ == "__main__":
    unittest.main()
