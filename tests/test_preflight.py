from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import mount_session
from liveusb.backend import mounts
from liveusb.backend import preflight
from liveusb.backend import rebuild
from liveusb.backend import transaction


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.mount_root = self.root / "mount"
        self.mount_root.mkdir(mode=0o700)
        self.source_iso = self.root / "source.iso"
        self.source_iso.write_bytes(b"synthetic source ISO")
        self.ctx = Context(
            work_dir=str(self.work),
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.root / "runtime"),
            iso=str(self.source_iso),
        )
        self.owner_uid = os.geteuid()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(mode=0o700)
        commands = {
            spec.command
            for spec in preflight.DEFAULT_DEPENDENCIES
        }
        commands.update(
            {
                "isoinfo",
                "qemu-system-i386",
                "qemu-system-x86_64",
                "sudo",
                "xorriso",
            }
        )
        for command in sorted(commands):
            executable = self.bin_dir / command
            executable.write_bytes(b"synthetic executable\n")
            executable.chmod(0o755)

    def engine(self, **overrides):
        def discover(command):
            candidate = self.bin_dir / command
            return str(candidate) if candidate.exists() else None

        def missing_kvm(_path):
            raise FileNotFoundError("Synthetic KVM node is absent")

        options = {
            "which": discover,
            "mountinfo_reader": lambda: tuple(),
            "lock_text_reader": lambda: "",
            "effective_uid": lambda: 0,
            "expected_owner_uid": self.owner_uid,
            "cpu_count_reader": lambda: 4,
            "loadavg_reader": lambda: (1.0, 2.0, 3.0),
            "meminfo_reader": lambda: (
                "MemTotal: 8000 kB\n"
                "MemAvailable: 6000 kB\n"
                "SwapTotal: 2000 kB\n"
                "SwapFree: 1500 kB\n"
            ),
            "machine_reader": lambda: "x86_64",
            "kvm_state_reader": missing_kvm,
        }
        options.update(overrides)
        return preflight.PreflightEngine(**options)

    @staticmethod
    def finding(report, check_id):
        return next(
            finding
            for finding in report.findings
            if finding.check_id == check_id
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

    def write_pair(self, stem, payload):
        iso = self.work / (stem + ".iso")
        sidecar = self.work / (stem + ".sha256")
        iso.write_bytes(payload)
        iso.chmod(0o555)
        digest = hashlib.sha256(payload).hexdigest()
        sidecar.write_text(
            "{}  {}\n".format(digest, iso.name),
            encoding="ascii",
        )
        return iso, sidecar, digest

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
                state.st_uid,
                state.st_gid,
                state.st_size,
                content,
            )
        return records

    def test_structured_contract_preserves_all_statuses_and_redacts(self):
        statuses = preflight.STATUS_ORDER
        findings = tuple(
            preflight.Finding(
                "synthetic." + status,
                "synthetic",
                status,
                "Synthetic {} finding".format(status),
                {
                    "api_token": "alpha",
                    "message": "Bearer beta",
                    "nested": {"password": "gamma"},
                    "safe": status,
                    "url": "https://user:delta@example.invalid/path",
                },
                "Set SECRET=epsilon only through approved custody.",
            )
            for status in statuses
        )
        report = preflight.PreflightReport(findings)

        value = report.to_dict()
        self.assertEqual(value["schema_version"], preflight.SCHEMA_VERSION)
        self.assertNotIn("overall", value)
        self.assertNotIn("overall_status", value)
        self.assertEqual(value["counts"], {status: 1 for status in statuses})
        encoded = report.to_json()
        self.assertEqual(encoded, report.to_json())
        json.loads(encoded)
        for secret in ("alpha", "beta", "gamma", "delta", "epsilon"):
            self.assertNotIn(secret, encoded)
        rendered = report.render_text()
        for status in statuses:
            self.assertIn("[{}]".format(status.upper()), rendered)
        for finding in findings:
            encoded_evidence = json.dumps(
                finding.to_dict()["evidence"],
                sort_keys=True,
                separators=(",", ":"),
            )
            self.assertIn("evidence=" + encoded_evidence, rendered)
        self.assertIn("no aggregate verdict", rendered)
        self.assertNotIn("alpha", rendered)

    def test_valid_legacy_preflight_is_stable_and_observation_only(self):
        self.make_legacy_tree()
        before = self.snapshot()

        report = self.engine().inspect(self.ctx)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(
            self.finding(report, "workspace.work-root").status,
            preflight.STATUS_PASS,
        )
        self.assertEqual(
            self.finding(report, "workspace.layout").evidence["state"],
            "extracted",
        )
        profile = self.finding(
            report,
            "media.legacy-extracted-profile",
        )
        self.assertEqual(profile.status, preflight.STATUS_PASS)
        self.assertTrue(profile.evidence["recognized"])
        self.assertEqual(
            profile.evidence["profile"],
            "legacy-isolinux-single-filesystem-extracted-tree",
        )
        self.assertEqual(
            self.finding(report, "mount.workspace").status,
            preflight.STATUS_PASS,
        )
        self.assertEqual(
            self.finding(report, "publication.prior-pair").status,
            preflight.STATUS_PASS,
        )
        for finding in report.findings:
            self.assertTrue(finding.evidence)
            self.assertTrue(finding.remediation)
        json.dumps(report.to_dict(), sort_keys=True)

    def test_empty_workspace_is_ready_and_profile_is_skipped(self):
        report = self.engine().inspect(self.ctx)

        self.assertEqual(
            self.finding(report, "workspace.layout").evidence["state"],
            "empty",
        )
        self.assertEqual(
            self.finding(
                report,
                "media.legacy-extracted-profile",
            ).status,
            preflight.STATUS_SKIPPED,
        )

    def test_path_failure_does_not_hide_independent_findings(self):
        ctx = Context(
            work_dir="relative-work",
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.root / "runtime"),
            iso="",
        )
        report = self.engine().inspect(ctx)

        self.assertEqual(
            self.finding(report, "workspace.work-root").status,
            preflight.STATUS_FAIL,
        )
        self.assertEqual(
            self.finding(report, "capacity.workspace").status,
            preflight.STATUS_SKIPPED,
        )
        self.assertEqual(
            self.finding(report, "publication.prior-pair").status,
            preflight.STATUS_SKIPPED,
        )
        self.assertEqual(
            self.finding(report, "input.source-iso").status,
            preflight.STATUS_SKIPPED,
        )
        self.assertEqual(set(report.counts), set(preflight.STATUS_ORDER))

    def test_workspace_confinement_rejects_each_destructive_overlap(self):
        cases = (
            {
                "mount_dir": str(self.work / "mount"),
                "runtime_dir": str(self.root / "runtime"),
                "iso": str(self.source_iso),
                "issue": "mount-root-overlaps-workspace",
            },
            {
                "mount_dir": str(self.mount_root),
                "runtime_dir": str(self.work / "runtime"),
                "iso": str(self.source_iso),
                "issue": "runtime-root-is-inside-workspace",
            },
            {
                "mount_dir": str(self.mount_root),
                "runtime_dir": str(self.root / "runtime"),
                "iso": str(self.work / "source.iso"),
                "issue": "source-iso-is-inside-workspace",
            },
        )
        for case in cases:
            with self.subTest(issue=case["issue"]):
                ctx = Context(
                    work_dir=str(self.work),
                    mount_dir=case["mount_dir"],
                    runtime_dir=case["runtime_dir"],
                    iso=case["iso"],
                )
                finding = self.finding(
                    self.engine().inspect(ctx),
                    "workspace.confinement",
                )
                self.assertEqual(finding.status, preflight.STATUS_FAIL)
                self.assertIn(case["issue"], finding.evidence["issues"])

        clean = self.finding(
            self.engine().inspect(self.ctx),
            "workspace.confinement",
        )
        self.assertEqual(clean.status, preflight.STATUS_PASS)
        self.assertEqual(clean.evidence["issues"], [])

    def test_symlinked_workspace_and_owner_mismatch_fail(self):
        real_work = self.root / "real-work"
        real_work.mkdir()
        linked_work = self.root / "linked-work"
        linked_work.symlink_to(real_work, target_is_directory=True)
        ctx = Context(
            work_dir=str(linked_work),
            mount_dir=str(self.mount_root),
            runtime_dir=str(self.root / "runtime"),
            iso=str(self.source_iso),
        )
        linked_report = self.engine().inspect(ctx)
        linked = self.finding(linked_report, "workspace.work-root")
        self.assertEqual(linked.status, preflight.STATUS_FAIL)
        self.assertIn("symlink", " ".join(linked.evidence["issues"]))

        owner_report = self.engine(
            expected_owner_uid=self.owner_uid + 1,
        ).inspect(self.ctx)
        owner = self.finding(owner_report, "workspace.work-root")
        self.assertEqual(owner.status, preflight.STATUS_FAIL)
        self.assertIn("owner-mismatch", owner.evidence["issues"])

    def test_legacy_profile_rejects_hard_linked_boot_image(self):
        self.make_legacy_tree()
        boot_image = self.work / "ISO/isolinux/isolinux.bin"
        payload = boot_image.read_bytes()
        boot_image.unlink()
        source = self.work / "boot-image-source"
        source.write_bytes(payload)
        os.link(source, boot_image)

        report = self.engine().inspect(self.ctx)
        profile = self.finding(
            report,
            "media.legacy-extracted-profile",
        )

        self.assertEqual(profile.status, preflight.STATUS_FAIL)
        record = next(
            item
            for item in profile.evidence["required_nodes"]
            if item["relative_path"] == "isolinux/isolinux.bin"
        )
        self.assertIn("link-count-is-not-one", record["issues"])
        self.assertEqual(os.stat(source).st_nlink, 2)
        self.assertEqual(source.read_bytes(), payload)
        self.assertEqual(boot_image.read_bytes(), payload)

    def test_source_iso_pass_fail_and_skip_are_explicit(self):
        passed = self.finding(
            self.engine().inspect(self.ctx),
            "input.source-iso",
        )
        self.assertEqual(passed.status, preflight.STATUS_PASS)
        self.assertEqual(
            passed.evidence["node"]["inode"],
            os.lstat(self.source_iso).st_ino,
        )
        self.assertEqual(
            passed.evidence["sha256"],
            hashlib.sha256(self.source_iso.read_bytes()).hexdigest(),
        )
        self.assertTrue(passed.evidence["content_observed"])

        self.ctx.iso = str(self.root / "missing.iso")
        failed = self.finding(
            self.engine().inspect(self.ctx),
            "input.source-iso",
        )
        self.assertEqual(failed.status, preflight.STATUS_FAIL)

        self.ctx.iso = ""
        skipped = self.finding(
            self.engine().inspect(self.ctx),
            "input.source-iso",
        )
        self.assertEqual(skipped.status, preflight.STATUS_SKIPPED)

    def test_source_hash_changes_in_place_and_rejects_read_mutation(self):
        first = self.finding(
            self.engine().inspect(self.ctx),
            "input.source-iso",
        )
        first_inode = first.evidence["node"]["inode"]
        first_digest = first.evidence["sha256"]
        replacement = bytes(
            (value + 17) % 256
            for value in self.source_iso.read_bytes()
        )
        with self.source_iso.open("r+b") as handle:
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())

        second = self.finding(
            self.engine().inspect(self.ctx),
            "input.source-iso",
        )
        self.assertEqual(second.status, preflight.STATUS_PASS)
        self.assertEqual(second.evidence["node"]["inode"], first_inode)
        self.assertNotEqual(second.evidence["sha256"], first_digest)

        source_state = os.stat(self.source_iso)
        real_read = os.read
        mutation_performed = []

        def mutate_during_read(descriptor, size):
            payload = real_read(descriptor, size)
            descriptor_state = os.fstat(descriptor)
            if (
                payload
                and not mutation_performed
                and descriptor_state.st_dev == source_state.st_dev
                and descriptor_state.st_ino == source_state.st_ino
            ):
                with self.source_iso.open("r+b") as handle:
                    handle.write(b"Q" * len(replacement))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.utime(
                    self.source_iso,
                    ns=(
                        source_state.st_atime_ns,
                        source_state.st_mtime_ns + 1_000_000,
                    ),
                )
                mutation_performed.append(True)
            return payload

        with mock.patch.object(os, "read", side_effect=mutate_during_read):
            changed = self.finding(
                self.engine().inspect(self.ctx),
                "input.source-iso",
            )

        self.assertEqual(mutation_performed, [True])
        self.assertEqual(changed.status, preflight.STATUS_FAIL)
        self.assertFalse(changed.evidence["content_observed"])

    def test_non_root_observation_is_separate_from_factory_authority(self):
        report = self.engine(
            effective_uid=lambda: 1000,
        ).inspect(self.ctx)
        current = self.finding(report, "privilege.current")
        sudo_path = self.finding(report, "privilege.sudo-path")
        authority = self.finding(
            report,
            "privilege.factory-authorization",
        )

        self.assertEqual(current.status, preflight.STATUS_PASS)
        self.assertFalse(current.evidence["is_root"])
        self.assertFalse(current.evidence["preflight_requires_root"])
        self.assertEqual(sudo_path.status, preflight.STATUS_PASS)
        self.assertFalse(
            sudo_path.evidence["escalation_test_executed"]
        )
        self.assertEqual(authority.status, preflight.STATUS_UNKNOWN)
        self.assertFalse(
            authority.evidence["factory_authority_evaluated"]
        )
        self.assertFalse(
            authority.evidence["factory_authority_granted"]
        )

    def test_accepted_authority_interfaces_and_versions_are_reused(self):
        self.make_legacy_tree()
        iso, sidecar, digest = self.write_pair(
            "accepted-authority",
            b"accepted final bytes",
        )

        with mock.patch.object(
            mounts,
            "validate_extract_layout",
            wraps=mounts.validate_extract_layout,
        ) as validate_layout, mock.patch.object(
            mounts,
            "literal_directory_chain",
            wraps=mounts.literal_directory_chain,
        ) as literal_chain, mock.patch.object(
            mounts,
            "iso_source_identity",
            wraps=mounts.iso_source_identity,
        ) as source_identity, mock.patch.object(
            rebuild,
            "_validate_prior_pair",
            wraps=rebuild._validate_prior_pair,
        ) as validate_pair, mock.patch.object(
            rebuild,
            "_sidecar_payload",
            wraps=rebuild._sidecar_payload,
        ) as sidecar_payload:
            report = self.engine().inspect(self.ctx)

        validate_layout.assert_called()
        literal_chain.assert_called()
        self.assertEqual(source_identity.call_count, 2)
        validate_pair.assert_called_once_with(str(iso), str(sidecar))
        self.assertGreaterEqual(sidecar_payload.call_count, 2)
        pair = self.finding(
            report,
            "publication.prior-pair",
        ).evidence["valid_pairs"][0]
        self.assertEqual(pair["sha256"], digest)
        self.assertTrue(pair["accepted_publication_semantics"])
        self.assertEqual(
            preflight._MOUNT_JOURNAL_VERSION,
            mount_session._JOURNAL_VERSION,
        )
        self.assertEqual(
            preflight._CHROOT_JOURNAL_VERSION,
            transaction._JOURNAL_VERSION,
        )
        self.assertEqual(
            preflight._CHROOT_LOCK_VERSION,
            transaction._LOCK_VERSION,
        )

    def test_capacity_reports_exact_values_without_threshold(self):
        capacity = types.SimpleNamespace(
            f_bavail=1,
            f_bfree=2,
            f_blocks=3,
            f_bsize=4096,
            f_frsize=4096,
        )
        report = self.engine(
            statvfs=lambda _path: capacity,
        ).inspect(self.ctx)
        finding = self.finding(report, "capacity.workspace")

        self.assertEqual(finding.status, preflight.STATUS_PASS)
        self.assertEqual(finding.evidence["available_bytes"], 4096)
        self.assertEqual(finding.evidence["free_bytes"], 8192)
        self.assertEqual(finding.evidence["total_bytes"], 12288)
        sufficiency = self.finding(report, "capacity.sufficiency")
        self.assertEqual(sufficiency.status, preflight.STATUS_UNKNOWN)
        self.assertIsNone(sufficiency.evidence["requirement_bytes"])
        self.assertFalse(sufficiency.evidence["sufficiency_evaluated"])

    def test_dependency_discovery_never_executes_or_installs(self):
        present = self.bin_dir / "present"
        present.write_bytes(b"present")
        present.chmod(0o755)
        specs = (
            preflight.DependencySpec("present", "test"),
            preflight.DependencySpec("missing", "test"),
            preflight.DependencySpec("unknown", "test"),
        )

        def discover(command):
            if command == "present":
                return str(present)
            if command == "unknown":
                raise OSError("sensitive discovery detail")
            return None

        with mock.patch.object(subprocess, "run") as run, mock.patch.object(
            os,
            "system",
        ) as system:
            report = self.engine(
                dependencies=specs,
                which=discover,
            ).inspect(self.ctx)

        run.assert_not_called()
        system.assert_not_called()
        self.assertEqual(
            self.finding(report, "dependency.present").status,
            preflight.STATUS_PASS,
        )
        present_finding = self.finding(report, "dependency.present")
        self.assertTrue(present_finding.evidence["discovered"])
        self.assertFalse(
            present_finding.evidence["version_query_executed"]
        )
        self.assertEqual(
            present_finding.evidence["version_status"],
            "unobserved-until-phase-1e-b",
        )
        self.assertEqual(
            self.finding(report, "dependency.missing").status,
            preflight.STATUS_FAIL,
        )
        missing = self.finding(report, "dependency.missing")
        self.assertFalse(missing.evidence["discovered"])
        self.assertFalse(missing.evidence["version_query_executed"])
        unknown = self.finding(report, "dependency.unknown")
        self.assertEqual(unknown.status, preflight.STATUS_UNKNOWN)
        self.assertEqual(unknown.evidence["error_type"], "OSError")
        self.assertNotIn("sensitive", report.to_json())

    def test_source_inspector_selection_is_available_but_unexecuted(self):
        def discovery(available):
            def discover(command):
                if (
                    command in {"isoinfo", "xorriso"}
                    and command not in available
                ):
                    return None
                candidate = self.bin_dir / command
                return str(candidate) if candidate.exists() else None

            return discover

        cases = (
            ({"isoinfo", "xorriso"}, preflight.STATUS_PASS, "isoinfo"),
            ({"xorriso"}, preflight.STATUS_PASS, "xorriso"),
            (set(), preflight.STATUS_FAIL, None),
        )
        for available, expected_status, selected in cases:
            with self.subTest(available=sorted(available)):
                report = self.engine(
                    which=discovery(available),
                ).inspect(self.ctx)
                inspector = self.finding(
                    report,
                    "media.source-inspector",
                )
                profile = self.finding(report, "media.source-profile")
                self.assertEqual(inspector.status, expected_status)
                self.assertEqual(
                    inspector.evidence["selected_provider"],
                    selected,
                )
                self.assertFalse(
                    inspector.evidence["inspection_executed"]
                )
                self.assertEqual(
                    profile.status,
                    preflight.STATUS_UNKNOWN,
                )
                self.assertFalse(
                    profile.evidence["inspection_executed"]
                )
                self.assertIsNone(profile.evidence["profile_result"])

    def test_qemu_and_kvm_readiness_preserve_tcg_fallback(self):
        kvm_state = types.SimpleNamespace(
            st_dev=1,
            st_gid=self.owner_uid,
            st_ino=72,
            st_mode=stat.S_IFCHR | 0o660,
            st_nlink=1,
            st_size=0,
            st_uid=self.owner_uid,
        )

        def access_with_kvm(allowed):
            def observe(path, mode):
                if path == "/dev/kvm":
                    self.assertEqual(mode, os.R_OK | os.W_OK)
                    return allowed
                return os.access(path, mode)

            return observe

        available = self.engine(
            kvm_state_reader=lambda _path: kvm_state,
            access=access_with_kvm(True),
        ).inspect(self.ctx)
        self.assertEqual(
            self.finding(available, "qemu.binary").evidence["command"],
            "qemu-system-x86_64",
        )
        self.assertEqual(
            self.finding(available, "qemu.kvm").status,
            preflight.STATUS_PASS,
        )

        inaccessible = self.engine(
            kvm_state_reader=lambda _path: kvm_state,
            access=access_with_kvm(False),
        ).inspect(self.ctx)
        inaccessible_kvm = self.finding(inaccessible, "qemu.kvm")
        self.assertEqual(
            inaccessible_kvm.status,
            preflight.STATUS_WARNING,
        )
        self.assertEqual(
            inaccessible_kvm.evidence["acceleration_fallback"],
            "tcg",
        )

        absent = self.finding(
            self.engine().inspect(self.ctx),
            "qemu.kvm",
        )
        self.assertEqual(absent.status, preflight.STATUS_WARNING)
        self.assertFalse(absent.evidence["exists"])

        alternate = self.engine(
            machine_reader=lambda: "i686",
        ).inspect(self.ctx)
        self.assertEqual(
            self.finding(alternate, "qemu.binary").evidence["command"],
            "qemu-system-i386",
        )
        contract = self.finding(
            available,
            "qemu.acceptance-contract",
        )
        self.assertEqual(contract.status, preflight.STATUS_UNKNOWN)
        self.assertEqual(
            contract.evidence["accepted_boot_path"],
            "bios-cdrom",
        )
        self.assertEqual(
            contract.evidence["excluded"],
            ("uefi", "ovmf", "usb-emulation"),
        )
        self.assertFalse(contract.evidence["boot_executed"])

    def test_machine_resources_report_facts_ratios_and_reader_failures(self):
        report = self.engine().inspect(self.ctx)
        cpu = self.finding(report, "resource.cpu")
        load = self.finding(report, "resource.load")
        memory = self.finding(report, "resource.memory")

        self.assertEqual(cpu.status, preflight.STATUS_PASS)
        self.assertEqual(cpu.evidence["logical_cpu_count"], 4)
        self.assertIsNone(cpu.evidence["threshold"])
        self.assertEqual(load.status, preflight.STATUS_PASS)
        self.assertEqual(load.evidence["load_1m"], 1.0)
        self.assertEqual(load.evidence["load_per_cpu_15m"], 0.75)
        self.assertIsNone(load.evidence["threshold"])
        self.assertEqual(memory.status, preflight.STATUS_PASS)
        self.assertEqual(
            memory.evidence["memory_total_bytes"],
            8000 * 1024,
        )
        self.assertEqual(
            memory.evidence["memory_available_ratio"],
            0.75,
        )
        self.assertEqual(
            memory.evidence["swap_available_ratio"],
            0.75,
        )
        self.assertIsNone(memory.evidence["threshold"])

        def unavailable():
            raise OSError("Synthetic observation failure")

        failed = self.engine(
            cpu_count_reader=unavailable,
            loadavg_reader=unavailable,
            meminfo_reader=unavailable,
        ).inspect(self.ctx)
        for check_id in (
            "resource.cpu",
            "resource.load",
            "resource.memory",
        ):
            finding = self.finding(failed, check_id)
            self.assertEqual(finding.status, preflight.STATUS_UNKNOWN)
            self.assertEqual(finding.evidence["error_type"], "OSError")

    def test_operation_plan_is_ordered_and_explicitly_deferred(self):
        report = self.engine().inspect(self.ctx)
        plan = self.finding(report, "factory.operation-plan")

        self.assertEqual(plan.status, preflight.STATUS_UNKNOWN)
        self.assertEqual(
            plan.evidence["ordered_stages"],
            preflight._FACTORY_STAGES,
        )
        self.assertEqual(plan.evidence["commands_executed"], 0)
        self.assertFalse(plan.evidence["exact_argv_captured"])
        self.assertFalse(plan.evidence["factory_authority_granted"])
        self.assertIn(
            "bounded capability probes",
            plan.evidence["phase_1e_b_responsibility"],
        )
        self.assertIn(
            "source-media-profile",
            plan.evidence["unresolved_inputs"],
        )

    def test_active_mount_and_unreadable_mountinfo_are_independent(self):
        identity = mounts.MountIdentity(
            mount_id=41,
            parent_id=1,
            major_minor="0:41",
            root="/",
            mount_point=str(self.work / "ISO"),
            mount_options=("rw",),
            optional_fields=(),
            fs_type="tmpfs",
            source="tmpfs",
            super_options=("rw",),
        )
        active_report = self.engine(
            mountinfo_reader=lambda: (identity,),
        ).inspect(self.ctx)
        active = self.finding(active_report, "mount.workspace")
        self.assertEqual(active.status, preflight.STATUS_FAIL)
        self.assertEqual(active.evidence["active_mount_count"], 1)
        self.assertEqual(
            active.evidence["active_mounts"][0]["mount_id"],
            41,
        )

        def unreadable():
            raise PermissionError("mountinfo detail")

        unknown_report = self.engine(
            mountinfo_reader=unreadable,
        ).inspect(self.ctx)
        unknown = self.finding(unknown_report, "mount.workspace")
        self.assertEqual(unknown.status, preflight.STATUS_UNKNOWN)
        self.assertEqual(unknown.evidence["error_type"], "PermissionError")
        self.assertNotIn("detail", unknown_report.to_json())

    def test_lock_reports_live_holder_without_acquiring_lock(self):
        runtime = Path(self.ctx.runtime_dir)
        runtime.mkdir(mode=0o700)
        lock = runtime / "operation.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        state = os.lstat(lock)
        lock_line = (
            "1: FLOCK ADVISORY WRITE 4321 "
            "{:x}:{:x}:{} 0 EOF\n".format(
                os.major(state.st_dev),
                os.minor(state.st_dev),
                state.st_ino,
            )
        )
        before = self.snapshot()

        report = self.engine(
            lock_text_reader=lambda: lock_line,
        ).inspect(self.ctx)
        finding = self.finding(report, "lock.operation")

        self.assertEqual(finding.status, preflight.STATUS_FAIL)
        self.assertTrue(finding.evidence["held"])
        self.assertEqual(finding.evidence["holders"][0]["pid"], 4321)
        self.assertEqual(self.snapshot(), before)

        unlocked = self.finding(
            self.engine().inspect(self.ctx),
            "lock.operation",
        )
        self.assertEqual(unlocked.status, preflight.STATUS_PASS)
        self.assertFalse(unlocked.evidence["held"])

    def test_malformed_lock_evidence_and_hard_link_fail_closed(self):
        runtime = Path(self.ctx.runtime_dir)
        runtime.mkdir(mode=0o700)
        lock = runtime / "operation.lock"
        source = runtime / "lock-source"
        source.write_bytes(b"")
        source.chmod(0o600)
        os.link(source, lock)

        hard_linked = self.finding(
            self.engine().inspect(self.ctx),
            "lock.operation",
        )
        self.assertEqual(hard_linked.status, preflight.STATUS_FAIL)
        self.assertIn(
            "link-count-is-not-one",
            hard_linked.evidence["issues"],
        )

        lock.unlink()
        source.unlink()
        lock.write_bytes(b"")
        lock.chmod(0o600)
        unknown = self.finding(
            self.engine(
                lock_text_reader=lambda: "malformed lock record\n",
            ).inspect(self.ctx),
            "lock.operation",
        )
        self.assertEqual(unknown.status, preflight.STATUS_UNKNOWN)
        self.assertEqual(unknown.evidence["error_type"], "ValueError")

    def test_journal_reports_readable_pending_and_corrupt_states(self):
        runtime = Path(self.ctx.runtime_dir)
        runtime.mkdir(mode=0o700)
        operation_lock = runtime / "operation.lock"
        operation_lock.write_bytes(b"")
        operation_lock.chmod(0o600)
        journal = runtime / "mount-session.json"
        journal.write_text(
            json.dumps(
                {
                    "artifacts": [],
                    "directories": [],
                    "external": None,
                    "mounts": [],
                    "owner": {
                        "pid": 1234,
                        "token": "a" * 32,
                    },
                    "phase": "active",
                    "previous_sha256": "b" * 64,
                    "roots": {
                        "filesystem": os.path.realpath(self.ctx.fs_dir),
                        "work": os.path.realpath(self.ctx.work_dir),
                    },
                    "sequence": 7,
                    "version": mount_session._JOURNAL_VERSION,
                    "x": {
                        "before": None,
                        "mutation": False,
                        "stage": "unexamined",
                    },
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        report = self.engine().inspect(self.ctx)
        finding = self.finding(report, "journal.mount-session")
        self.assertEqual(finding.status, preflight.STATUS_WARNING)
        self.assertEqual(finding.evidence["metadata"]["sequence"], 7)
        self.assertNotIn("a" * 32, report.to_json())

        pending = runtime / "mount-session.json.pending-deadbeef"
        pending.write_bytes(b"pending")
        pending.chmod(0o600)
        pending_finding = self.finding(
            self.engine().inspect(self.ctx),
            "journal.mount-session",
        )
        self.assertEqual(pending_finding.status, preflight.STATUS_FAIL)
        self.assertEqual(pending_finding.evidence["pending_count"], 1)

        pending.unlink()
        journal.write_bytes(b"{corrupt")
        journal.chmod(0o600)
        corrupt = self.finding(
            self.engine().inspect(self.ctx),
            "journal.mount-session",
        )
        self.assertEqual(corrupt.status, preflight.STATUS_FAIL)
        self.assertEqual(corrupt.evidence["error_type"], "JSONDecodeError")

    def test_journal_rejects_unknown_schema_without_reporting_token(self):
        runtime = Path(self.ctx.runtime_dir)
        runtime.mkdir(mode=0o700)
        operation_lock = runtime / "operation.lock"
        operation_lock.write_bytes(b"")
        operation_lock.chmod(0o600)
        journal = runtime / "mount-session.json"
        journal.write_text(
            json.dumps(
                {
                    "owner": {
                        "pid": 1234,
                        "token": "never-report-this-token",
                    },
                    "sequence": 1,
                    "version": mount_session._JOURNAL_VERSION,
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        finding = self.finding(
            self.engine().inspect(self.ctx),
            "journal.mount-session",
        )

        self.assertEqual(finding.status, preflight.STATUS_FAIL)
        self.assertIn(
            "top-level-schema",
            " ".join(finding.evidence["schema_issues"]),
        )
        self.assertNotIn("never-report", finding.to_dict().__str__())

    def test_pending_only_journal_is_a_separate_failure(self):
        runtime = Path(self.ctx.runtime_dir)
        runtime.mkdir(mode=0o700)
        pending = runtime / "mount-session.json.pending-only"
        pending.write_bytes(b"pending")
        pending.chmod(0o600)

        finding = self.finding(
            self.engine().inspect(self.ctx),
            "journal.mount-session",
        )

        self.assertEqual(finding.status, preflight.STATUS_FAIL)
        self.assertFalse(finding.evidence["exists"])
        self.assertEqual(finding.evidence["pending_count"], 1)

    def test_chroot_lock_and_journal_require_recognized_custody(self):
        self.make_legacy_tree()
        lock = self.work / "FileSystem/tmp/lock_chroot"
        lock.write_text(
            json.dumps(
                {
                    "pid": 4321,
                    "token": "c" * 32,
                    "version": transaction._LOCK_VERSION,
                }
            ),
            encoding="utf-8",
        )
        lock.chmod(0o600)
        journal = self.work / ".liveusb-chroot-transaction.json"
        journal.write_text(
            json.dumps(
                {
                    "managed": [],
                    "owner": {
                        "pid": 4321,
                        "token": "c" * 32,
                    },
                    "sequence": 2,
                    "services": [],
                    "version": transaction._JOURNAL_VERSION,
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)

        report = self.engine().inspect(self.ctx)
        lock_finding = self.finding(report, "lock.chroot")
        journal_finding = self.finding(
            report,
            "journal.chroot-transaction",
        )
        self.assertEqual(lock_finding.status, preflight.STATUS_WARNING)
        self.assertEqual(lock_finding.evidence["metadata"]["owner_pid"], 4321)
        self.assertEqual(journal_finding.status, preflight.STATUS_WARNING)
        self.assertEqual(
            journal_finding.evidence["metadata"]["managed_count"],
            0,
        )
        self.assertNotIn("c" * 32, report.to_json())

        lock.unlink()
        orphaned = self.finding(
            self.engine().inspect(self.ctx),
            "journal.chroot-transaction",
        )
        self.assertEqual(orphaned.status, preflight.STATUS_FAIL)
        self.assertIn(
            "journal-exists-without-lock-inode",
            orphaned.evidence["issues"],
        )

        lock.write_text("not JSON", encoding="utf-8")
        lock.chmod(0o600)
        corrupt_report = self.engine().inspect(self.ctx)
        corrupt_lock = self.finding(corrupt_report, "lock.chroot")
        corrupt_journal = self.finding(
            corrupt_report,
            "journal.chroot-transaction",
        )
        self.assertEqual(corrupt_lock.status, preflight.STATUS_FAIL)
        self.assertIn(
            "metadata-is-unreadable",
            " ".join(corrupt_lock.evidence["issues"]),
        )
        self.assertEqual(corrupt_journal.status, preflight.STATUS_FAIL)
        self.assertIn(
            "journal-lock-custody-is-invalid",
            corrupt_journal.evidence["issues"],
        )

    def test_valid_prior_pair_reports_final_bytes_and_inode_evidence(self):
        iso, sidecar, digest = self.write_pair(
            "Ubuntu-amd64-14.04",
            b"final ISO bytes",
        )

        report = self.engine().inspect(self.ctx)
        finding = self.finding(report, "publication.prior-pair")

        self.assertEqual(finding.status, preflight.STATUS_PASS)
        self.assertEqual(finding.evidence["pair_count"], 1)
        pair = finding.evidence["valid_pairs"][0]
        self.assertEqual(pair["sha256"], digest)
        self.assertEqual(pair["iso_node"]["inode"], os.stat(iso).st_ino)
        self.assertEqual(
            pair["sidecar_node"]["inode"],
            os.stat(sidecar).st_ino,
        )

    def test_publication_orphan_hard_link_and_residue_fail_closed(self):
        orphan = self.work / "orphan.iso"
        orphan.write_bytes(b"orphan")
        orphan.chmod(0o555)
        orphan_finding = self.finding(
            self.engine().inspect(self.ctx),
            "publication.prior-pair",
        )
        self.assertEqual(orphan_finding.status, preflight.STATUS_FAIL)
        self.assertEqual(
            orphan_finding.evidence["invalid_pairs"][0]["issue"],
            "orphaned-pair",
        )
        orphan.unlink()

        source = self.work / "hard-link-source"
        source.write_bytes(b"hard-linked ISO")
        source.chmod(0o555)
        hard_link = self.work / "hard-linked.iso"
        os.link(source, hard_link)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        sidecar = self.work / "hard-linked.sha256"
        sidecar.write_text(
            "{}  {}\n".format(digest, hard_link.name),
            encoding="ascii",
        )
        before = self.snapshot()
        linked_finding = self.finding(
            self.engine().inspect(self.ctx),
            "publication.prior-pair",
        )
        self.assertEqual(linked_finding.status, preflight.STATUS_FAIL)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(os.stat(source).st_nlink, 2)

        hard_link.unlink()
        source.unlink()
        sidecar.unlink()
        residue = self.work / (
            ".liveusb-publish-never-report-this-token-primary.candidate"
        )
        residue.write_bytes(b"candidate")
        residue_finding = self.finding(
            self.engine().inspect(self.ctx),
            "publication.prior-pair",
        )
        self.assertEqual(residue_finding.status, preflight.STATUS_FAIL)
        self.assertEqual(
            residue_finding.evidence["publication_residue_count"],
            1,
        )
        self.assertNotIn("never-report", residue_finding.to_dict().__str__())

    def test_multiple_valid_prior_pairs_are_warning_not_collapse(self):
        self.write_pair("first", b"first")
        self.write_pair("second", b"second")

        finding = self.finding(
            self.engine().inspect(self.ctx),
            "publication.prior-pair",
        )

        self.assertEqual(finding.status, preflight.STATUS_WARNING)
        self.assertEqual(finding.evidence["pair_count"], 2)
        self.assertEqual(len(finding.evidence["valid_pairs"]), 2)

    def test_runtime_parent_rejects_unsafe_writable_ancestor(self):
        unsafe = self.root / "unsafe-runtime-parent"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        self.ctx.runtime_dir = str(unsafe / "runtime")

        finding = self.finding(
            self.engine().inspect(self.ctx),
            "workspace.runtime-root",
        )

        self.assertEqual(finding.status, preflight.STATUS_FAIL)
        self.assertIn(
            "unsafe-writable-ancestor",
            " ".join(finding.evidence["issues"]),
        )
        self.assertFalse(Path(self.ctx.runtime_dir).exists())

    def test_real_report_can_carry_all_five_statuses_concurrently(self):
        missing_mount = self.root / "missing-mount"
        self.ctx.mount_dir = str(missing_mount)
        specs = (preflight.DependencySpec("missing", "test"),)

        def unreadable_mounts():
            raise OSError("mount observation unavailable")

        report = self.engine(
            dependencies=specs,
            which=lambda _command: None,
            mountinfo_reader=unreadable_mounts,
        ).inspect(self.ctx)

        for status in preflight.STATUS_ORDER:
            self.assertGreater(report.counts[status], 0)
        self.assertEqual(
            self.finding(report, "dependency.missing").status,
            preflight.STATUS_FAIL,
        )
        self.assertEqual(
            self.finding(report, "workspace.mount-root").status,
            preflight.STATUS_WARNING,
        )
        self.assertEqual(
            self.finding(report, "mount.workspace").status,
            preflight.STATUS_UNKNOWN,
        )
        self.assertEqual(
            self.finding(
                report,
                "media.legacy-extracted-profile",
            ).status,
            preflight.STATUS_SKIPPED,
        )

    def test_module_contains_no_host_mutation_or_command_execution_calls(self):
        source_path = Path(preflight.__file__)
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        forbidden_os_calls = {
            "chmod",
            "chown",
            "link",
            "makedirs",
            "mkdir",
            "remove",
            "rename",
            "replace",
            "rmdir",
            "system",
            "unlink",
        }
        observed = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in forbidden_os_calls
            ):
                observed.append(node.func.attr)
        self.assertEqual(observed, [])
        self.assertNotIn("subprocess", preflight.__dict__)


if __name__ == "__main__":
    unittest.main()
