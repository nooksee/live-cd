from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from liveusb import messages
from liveusb.backend import Context
from liveusb.backend import rebuild
from liveusb.backend import mount_session


class RebuildFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self._configure_fixture(self.root)

    def _configure_fixture(self, root):
        self.work = root / "work"
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
            mount_dir=str(root / "mount"),
            runtime_dir=str(root / "runtime"),
            compression="xz",
        )
        self.iso_path = self.work / "Ubuntu-amd64-14.04.iso"
        self.sidecar_path = (
            self.work / "Ubuntu-amd64-14.04.sha256"
        )

    @staticmethod
    def result(returncode=0, stdout=""):
        return types.SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
        )

    @staticmethod
    def _supported_probe(command, **_kwargs):
        Path(command[2]).write_bytes(b"synthetic squashfs")
        return RebuildFinalizationTests.result(0)

    def _write_pair(self, iso_path, sidecar_path, payload):
        iso_path.write_bytes(payload)
        iso_path.chmod(0o555)
        digest = hashlib.sha256(payload).hexdigest()
        sidecar_path.write_bytes(
            rebuild._sidecar_payload(digest, str(iso_path))
        )
        return digest

    def _begin_generated_candidate(self, session, payload=b"new ISO"):
        view = session.begin_external_publication(
            str(self.iso_path),
            str(self.sidecar_path),
            "final-image",
        )
        candidate = Path(session.begin_external_primary_write())
        candidate.write_bytes(payload)
        session.finish_external_primary_write()
        return view, candidate

    def _seal_candidate(
        self,
        session,
        payload=b"new ISO",
    ):
        view, candidate = self._begin_generated_candidate(
            session,
            payload,
        )

        def mutate(command):
            self.assertEqual(
                command,
                ["/usr/bin/isohybrid", str(candidate)],
            )
            with candidate.open("ab") as handle:
                handle.write(b" + hybrid")
            return self.result(0)

        rebuild._apply_legacy_hybrid_mutation(
            self.ctx,
            session,
            runner=mutate,
            locator=lambda _name: "/usr/bin/isohybrid",
        )
        digest = rebuild._sha256_file(str(candidate))
        session.record_external_digest(digest)
        return view, candidate, digest

    def _ready_candidate(
        self,
        session,
        payload=b"new ISO",
    ):
        view, candidate, digest = self._seal_candidate(
            session,
            payload,
        )
        session.write_external_evidence(
            rebuild._sidecar_payload(
                digest,
                str(self.iso_path),
            )
        )
        return view, candidate, digest

    def _assert_no_publication_residue(self):
        self.assertFalse(
            any(
                path.name.startswith(".liveusb-publish-")
                for path in self.work.iterdir()
            )
        )
        runtime = Path(self.ctx.runtime_dir)
        if runtime.exists():
            self.assertFalse(
                (runtime / "mount-session.json").exists()
            )
            self.assertEqual(
                list(
                    runtime.glob(
                        "mount-session.json.pending-*"
                    )
                ),
                [],
            )

    def test_t01_capability_probe_precedes_one_real_tree_build(self):
        events = []

        def probe(command, **kwargs):
            events.append(("probe", tuple(command)))
            return self._supported_probe(command, **kwargs)

        def runner(command):
            events.append(("build", tuple(command)))
            Path(command[2]).write_bytes(b"real tree image")
            return self.result(0)

        command = rebuild._build_squashfs(
            self.ctx,
            str(
                self.iso_tree
                / "casper/filesystem.squashfs"
            ),
            probe=probe,
            runner=runner,
        )

        self.assertEqual(
            [event[0] for event in events],
            ["probe", "build"],
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if event[0] == "build"
                ]
            ),
            1,
        )
        probe_command = events[0][1]
        self.assertEqual(probe_command[0], "mksquashfs")
        self.assertNotEqual(probe_command[1], self.ctx.fs_dir)
        self.assertIn("-processors", probe_command)
        self.assertEqual(
            probe_command[
                probe_command.index("-processors") + 1
            ],
            "1",
        )
        self.assertIn("-no-progress", probe_command)
        self.assertEqual(command[-2:], ["-comp", "xz"])
        self.assertFalse(
            any(
                path.name.startswith(
                    ".liveusb-compression-probe-"
                )
                for path in self.work.iterdir()
            )
        )

    def test_t02_unrelated_squashfs_failure_is_not_retried(self):
        commands = []

        with self.assertRaises(messages.LiveUSBError):
            rebuild._build_squashfs(
                self.ctx,
                str(
                    self.iso_tree
                    / "casper/filesystem.squashfs"
                ),
                probe=self._supported_probe,
                runner=lambda command: (
                    commands.append(command)
                    or self.result(1)
                ),
            )

        self.assertEqual(len(commands), 1)

    @unittest.skipUnless(
        os.environ.get("LIVEUSB_REAL_PROBE_TEST") == "1",
        "real bounded compressor probe is an explicit gate",
    )
    def test_t03_real_squashfs_capability_probe(self):
        if shutil.which("mksquashfs") is None:
            self.skipTest("mksquashfs is unavailable")
        self.assertTrue(
            rebuild._compression_is_supported(
                "xz",
                scratch_root=str(self.work),
            )
        )
        self.assertFalse(
            rebuild._compression_is_supported(
                "definitely-invalid-liveusb-compressor",
                scratch_root=str(self.work),
            )
        )
        self.assertFalse(
            any(
                path.name.startswith(
                    ".liveusb-compression-probe-"
                )
                for path in self.work.iterdir()
            )
        )

    def test_t04_nonlegacy_profile_invokes_isohybrid_zero_times(self):
        runner = mock.Mock(return_value=self.result(0))
        with self.assertRaises(messages.LiveUSBError):
            with mount_session.MountSession(self.ctx) as session:
                self._begin_generated_candidate(session)
                (
                    self.iso_tree
                    / "isolinux/isolinux.bin"
                ).unlink()
                rebuild._apply_legacy_hybrid_mutation(
                    self.ctx,
                    session,
                    runner=runner,
                    locator=lambda _name: "/usr/bin/isohybrid",
                )

        runner.assert_not_called()
        self.assertFalse(self.iso_path.exists())
        self.assertFalse(self.sidecar_path.exists())
        self._assert_no_publication_residue()

    def test_t05_missing_isohybrid_fails_before_mutation(self):
        runner = mock.Mock(return_value=self.result(0))
        with self.assertRaisesRegex(
            messages.LiveUSBError,
            "isohybrid",
        ):
            with mount_session.MountSession(self.ctx) as session:
                self._begin_generated_candidate(session)
                rebuild._apply_legacy_hybrid_mutation(
                    self.ctx,
                    session,
                    runner=runner,
                    locator=lambda _name: None,
                )

        runner.assert_not_called()
        self.assertFalse(self.iso_path.exists())
        self.assertFalse(self.sidecar_path.exists())
        self._assert_no_publication_residue()

    def test_t06_mutation_precedes_read_only_sealing(self):
        events = []
        with mount_session.MountSession(self.ctx) as session:
            _view, candidate = self._begin_generated_candidate(
                session
            )
            original_seal = session.seal_external_primary

            def mutate(command):
                events.append(
                    (
                        "mutate",
                        os.stat(candidate).st_mode & 0o777,
                    )
                )
                candidate.write_bytes(b"hybrid bytes")
                return self.result(0)

            def seal(*args, **kwargs):
                events.append(("seal", None))
                return original_seal(*args, **kwargs)

            with mock.patch.object(
                session,
                "seal_external_primary",
                side_effect=seal,
            ):
                rebuild._apply_legacy_hybrid_mutation(
                    self.ctx,
                    session,
                    runner=mutate,
                    locator=lambda _name: "/usr/bin/isohybrid",
                )

            self.assertEqual(
                events,
                [("mutate", 0o600), ("seal", None)],
            )
            self.assertEqual(
                os.stat(candidate).st_mode & 0o777,
                0o555,
            )

    def test_t07_partial_isohybrid_failure_is_not_published(self):
        prior_digest = self._write_pair(
            self.iso_path,
            self.sidecar_path,
            b"prior valid ISO",
        )

        def partial_failure(command):
            with open(command[1], "ab") as handle:
                handle.write(b"partial hybrid mutation")
            return self.result(1)

        with self.assertRaises(messages.LiveUSBError):
            with mount_session.MountSession(self.ctx) as session:
                self._begin_generated_candidate(session)
                rebuild._apply_legacy_hybrid_mutation(
                    self.ctx,
                    session,
                    runner=partial_failure,
                    locator=lambda _name: "/usr/bin/isohybrid",
                )

        self.assertEqual(
            rebuild._validate_sha256_pair(
                str(self.iso_path),
                str(self.sidecar_path),
            ),
            prior_digest,
        )
        self._assert_no_publication_residue()

    def test_t08_pre_mutation_exception_publishes_nothing(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "before mutation",
        ):
            with mount_session.MountSession(self.ctx) as session:
                self._begin_generated_candidate(session)
                raise RuntimeError("before mutation")

        self.assertFalse(self.iso_path.exists())
        self.assertFalse(self.sidecar_path.exists())
        self._assert_no_publication_residue()

    def test_t09_hash_describes_post_isohybrid_sealed_bytes(self):
        with mount_session.MountSession(self.ctx) as session:
            _view, candidate, digest = self._ready_candidate(
                session
            )
            self.assertEqual(
                digest,
                hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
            session.publish_external_pair(
                validator=rebuild._validate_sha256_pair,
            )

        self.assertEqual(
            rebuild._validate_sha256_pair(
                str(self.iso_path),
                str(self.sidecar_path),
            ),
            digest,
        )
        self.assertEqual(
            self.sidecar_path.read_bytes(),
            rebuild._sidecar_payload(
                digest,
                str(self.iso_path),
            ),
        )
        self._assert_no_publication_residue()

    def test_t10_interrupted_sidecar_write_never_reaches_final(self):
        session = mount_session.MountSession(self.ctx)
        session.__enter__()
        try:
            _view, _candidate, digest = self._seal_candidate(
                session
            )
            evidence_path = session.external_publication_view(
                str(self.iso_path),
                str(self.sidecar_path),
                "final-image",
            )["evidence_candidate"]
            original_write = session._write_all

            def interrupted_write(descriptor, payload):
                descriptor_path = os.readlink(
                    f"/proc/self/fd/{descriptor}"
                )
                if descriptor_path == evidence_path:
                    os.write(descriptor, payload[:7])
                    raise OSError(
                        "interrupted evidence write"
                    )
                return original_write(descriptor, payload)

            with self.assertRaisesRegex(
                messages.LiveUSBError,
                "external evidence",
            ), mock.patch.object(
                session,
                "_write_all",
                side_effect=interrupted_write,
            ):
                session.write_external_evidence(
                    rebuild._sidecar_payload(
                        digest,
                        str(self.iso_path),
                    )
                )
        finally:
            session._release_runtime_lock()
            session._entered = False

        self.assertFalse(self.sidecar_path.exists())
        with mount_session.MountSession(self.ctx) as recovery:
            view = recovery.external_publication_view(
                str(self.iso_path),
                str(self.sidecar_path),
                "final-image",
            )
            self.assertEqual(view["phase"], "sealed")
            self.assertEqual(view["evidence_stage"], "planned")

    def test_t11_sidecar_failure_retries_without_image_build(self):
        with self.assertRaisesRegex(
            messages.LiveUSBError,
            "external evidence",
        ):
            with mount_session.MountSession(self.ctx) as session:
                _view, _candidate, digest = self._seal_candidate(
                    session
                )
                with mock.patch.object(
                    session,
                    "_create_external_file",
                    side_effect=OSError("sidecar unavailable"),
                ):
                    session.write_external_evidence(
                        rebuild._sidecar_payload(
                            digest,
                            str(self.iso_path),
                        )
                    )

        build_commands = []
        with mount_session.MountSession(self.ctx) as recovery:
            with mock.patch.object(
                rebuild,
                "run",
                side_effect=lambda command, **_kwargs: (
                    build_commands.append(command)
                ),
            ):
                rebuild._finish_external_publication(
                    recovery,
                    str(self.iso_path),
                    str(self.sidecar_path),
                )

        self.assertEqual(build_commands, [])
        rebuild._validate_sha256_pair(
            str(self.iso_path),
            str(self.sidecar_path),
        )
        self._assert_no_publication_residue()

    def test_run_rebuild_resumes_before_all_factory_work(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "retain sealed candidate",
        ):
            with mount_session.MountSession(self.ctx) as session:
                self._seal_candidate(session)
                raise RuntimeError("retain sealed candidate")

        forbidden = (
            mock.patch.object(
                rebuild.chroot,
                "update_distro_name",
                side_effect=AssertionError(
                    "distribution mutation executed"
                ),
            ),
            mock.patch.object(
                rebuild.chroot,
                "check_sources_list",
                side_effect=AssertionError(
                    "source-list check executed"
                ),
            ),
            mock.patch.object(
                rebuild.subprocess,
                "run",
                side_effect=AssertionError(
                    "subprocess factory work executed"
                ),
            ),
            mock.patch.object(
                rebuild,
                "_build_squashfs",
                side_effect=AssertionError(
                    "SquashFS build executed"
                ),
            ),
            mock.patch.object(
                rebuild,
                "run",
                side_effect=AssertionError(
                    "image command executed"
                ),
            ),
        )
        with mock.patch.object(
            rebuild.mounts,
            "check_fs_dir",
        ), mock.patch.object(
            rebuild.mounts,
            "check_lock",
        ), mock.patch.object(
            rebuild,
            "_report_rebuild_success",
        ) as report, forbidden[0] as update, forbidden[1] as sources, (
            forbidden[2]
        ) as process, forbidden[3] as squash, forbidden[4] as command:
            rebuild.run_rebuild(self.ctx)

        report.assert_called_once_with()
        update.assert_not_called()
        sources.assert_not_called()
        process.assert_not_called()
        squash.assert_not_called()
        command.assert_not_called()
        rebuild._validate_sha256_pair(
            str(self.iso_path),
            str(self.sidecar_path),
        )
        self._assert_no_publication_residue()

    def test_t12_prior_pair_survives_post_squashfs_failure(self):
        prior_digest = self._write_pair(
            self.iso_path,
            self.sidecar_path,
            b"prior valid ISO",
        )

        def build_squashfs(_ctx, output_path):
            Path(output_path).write_bytes(b"squashfs")

        with self.assertRaisesRegex(
            messages.LiveUSBError,
            "final image transaction",
        ) as caught:
            with mount_session.MountSession(self.ctx) as session:
                with mock.patch.object(
                    rebuild,
                    "_build_squashfs",
                    side_effect=build_squashfs,
                ), mock.patch.object(
                    rebuild.subprocess,
                    "run",
                    side_effect=OSError("manifest failure"),
                ):
                    rebuild._build_locked_final_image(
                        self.ctx,
                        session,
                        "Ubuntu",
                        "amd64",
                        "14.04",
                        "trusty",
                        str(self.iso_tree / ".disk"),
                        str(self.iso_tree / "casper"),
                        str(self.iso_path),
                        str(self.sidecar_path),
                    )

        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(
            str(caught.exception.__cause__),
            "manifest failure",
        )
        self.assertEqual(
            rebuild._validate_sha256_pair(
                str(self.iso_path),
                str(self.sidecar_path),
            ),
            prior_digest,
        )
        self._assert_no_publication_residue()

    def test_t13_prior_pair_replaced_only_after_candidate_ready(self):
        prior_digest = self._write_pair(
            self.iso_path,
            self.sidecar_path,
            b"prior valid ISO",
        )
        with mount_session.MountSession(self.ctx) as session:
            _view, _candidate, new_digest = (
                self._ready_candidate(session)
            )
            self.assertEqual(
                rebuild._validate_sha256_pair(
                    str(self.iso_path),
                    str(self.sidecar_path),
                ),
                prior_digest,
            )
            session.publish_external_pair(
                validator=rebuild._validate_sha256_pair,
            )

        self.assertNotEqual(new_digest, prior_digest)
        self.assertEqual(
            rebuild._validate_sha256_pair(
                str(self.iso_path),
                str(self.sidecar_path),
            ),
            new_digest,
        )
        self._assert_no_publication_residue()

    def test_t14_operation_lock_spans_generation_to_publication(self):
        with mount_session.MountSession(self.ctx) as session:
            self._begin_generated_candidate(session)
            for stage in ("generated", "ready"):
                with self.subTest(stage=stage):
                    with self.assertRaises(
                        mount_session.MountRecoveryError
                    ):
                        with mount_session.MountSession(self.ctx):
                            pass
                if stage == "generated":
                    _view, _candidate, digest = (
                        self._seal_candidate_from_generated(
                            session
                        )
                    )
                    session.write_external_evidence(
                        rebuild._sidecar_payload(
                            digest,
                            str(self.iso_path),
                        )
                    )
            session.publish_external_pair(
                validator=rebuild._validate_sha256_pair,
            )

        self._assert_no_publication_residue()

    def _seal_candidate_from_generated(self, session):
        view = session.external_publication_view(
            str(self.iso_path),
            str(self.sidecar_path),
            "final-image",
        )
        candidate = Path(view["primary_candidate"])

        def mutate(_command):
            with candidate.open("ab") as handle:
                handle.write(b" + hybrid")
            return self.result(0)

        rebuild._apply_legacy_hybrid_mutation(
            self.ctx,
            session,
            runner=mutate,
            locator=lambda _name: "/usr/bin/isohybrid",
        )
        digest = rebuild._sha256_file(str(candidate))
        session.record_external_digest(digest)
        return view, candidate, digest

    def test_t15_missing_binaries_are_liveusb_errors(self):
        with self.subTest(binary="mksquashfs-probe"):
            with self.assertRaises(messages.LiveUSBError):
                rebuild._compression_is_supported(
                    "xz",
                    probe=mock.Mock(
                        side_effect=FileNotFoundError(
                            "mksquashfs"
                        )
                    ),
                    scratch_root=str(self.work),
                )

        with self.subTest(binary="mksquashfs-build"):
            with self.assertRaises(messages.LiveUSBError):
                rebuild._build_squashfs(
                    self.ctx,
                    str(
                        self.iso_tree
                        / "casper/filesystem.squashfs"
                    ),
                    probe=self._supported_probe,
                    runner=mock.Mock(
                        side_effect=FileNotFoundError(
                            "mksquashfs"
                        )
                    ),
                )

        with self.subTest(binary="genisoimage"):
            with self.assertRaises(messages.LiveUSBError):
                with mount_session.MountSession(
                    self.ctx
                ) as session:
                    with mock.patch.object(
                        rebuild,
                        "_build_squashfs",
                        side_effect=lambda _ctx, output: (
                            Path(output).write_bytes(b"squashfs")
                        ),
                    ), mock.patch.object(
                        rebuild.subprocess,
                        "run",
                        return_value=self.result(
                            0,
                            "base-files 1\n",
                        ),
                    ), mock.patch.object(
                        rebuild,
                        "run",
                        side_effect=FileNotFoundError(
                            "genisoimage"
                        ),
                    ):
                        rebuild._build_locked_final_image(
                            self.ctx,
                            session,
                            "Ubuntu",
                            "amd64",
                            "14.04",
                            "trusty",
                            str(self.iso_tree / ".disk"),
                            str(self.iso_tree / "casper"),
                            str(self.iso_path),
                            str(self.sidecar_path),
                        )

        with self.subTest(binary="isohybrid"):
            with self.assertRaises(messages.LiveUSBError):
                with mount_session.MountSession(
                    self.ctx
                ) as session:
                    self._begin_generated_candidate(session)
                    rebuild._apply_legacy_hybrid_mutation(
                        self.ctx,
                        session,
                        runner=mock.Mock(
                            side_effect=FileNotFoundError(
                                "isohybrid"
                            )
                        ),
                        locator=lambda _name: (
                            "/usr/bin/isohybrid"
                        ),
                    )

        self._assert_no_publication_residue()

    def test_t16_success_requires_seal_hash_and_sidecar(self):
        with mount_session.MountSession(self.ctx) as session:
            self._begin_generated_candidate(session)
            with self.assertRaises(
                mount_session.MountAcquisitionError
            ):
                session.publish_external_pair(
                    validator=rebuild._validate_sha256_pair,
                )
            _view, _candidate, digest = (
                self._seal_candidate_from_generated(session)
            )
            with self.assertRaises(
                mount_session.MountAcquisitionError
            ):
                session.publish_external_pair(
                    validator=rebuild._validate_sha256_pair,
                )
            session.write_external_evidence(
                rebuild._sidecar_payload(
                    digest,
                    str(self.iso_path),
                )
            )
            session.publish_external_pair(
                validator=rebuild._validate_sha256_pair,
            )

        self.assertEqual(
            rebuild._validate_sha256_pair(
                str(self.iso_path),
                str(self.sidecar_path),
            ),
            digest,
        )
        self._assert_no_publication_residue()

    def test_publication_interruption_after_every_boundary_recovers(self):
        for boundary in range(7):
            with self.subTest(boundary=boundary):
                fixture = self.root / f"boundary-{boundary}"
                fixture.mkdir()
                fixture.chmod(0o700)
                self._configure_fixture(fixture)
                self._write_pair(
                    self.iso_path,
                    self.sidecar_path,
                    b"prior valid ISO",
                )
                session = mount_session.MountSession(self.ctx)
                session.__enter__()
                try:
                    _view, _candidate, digest = (
                        self._ready_candidate(session)
                    )
                    original = (
                        session
                        ._perform_external_publication_action
                    )

                    def interrupt(
                        record,
                        action_index,
                        validator=None,
                    ):
                        original(
                            record,
                            action_index,
                            validator=validator,
                        )
                        if action_index == boundary:
                            raise mount_session.MountRecoveryError(
                                f"boundary {boundary}"
                            )

                    with self.assertRaisesRegex(
                        mount_session.MountRecoveryError,
                        f"boundary {boundary}",
                    ), mock.patch.object(
                        session,
                        "_perform_external_publication_action",
                        side_effect=interrupt,
                    ):
                        session.publish_external_pair(
                            validator=(
                                rebuild._validate_sha256_pair
                            ),
                        )
                finally:
                    session._release_runtime_lock()
                    session._entered = False

                with mount_session.MountSession(
                    self.ctx
                ) as recovery:
                    rebuild._finish_external_publication(
                        recovery,
                        str(self.iso_path),
                        str(self.sidecar_path),
                    )

                self.assertEqual(
                    rebuild._validate_sha256_pair(
                        str(self.iso_path),
                        str(self.sidecar_path),
                    ),
                    digest,
                )
                self._assert_no_publication_residue()

    def test_altered_sealed_candidate_fails_closed(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "retain candidate",
        ):
            with mount_session.MountSession(self.ctx) as session:
                _view, candidate, _digest = self._seal_candidate(
                    session
                )
                raise RuntimeError("retain candidate")

        candidate.chmod(0o644)
        candidate.write_bytes(b"foreign replacement")
        candidate.chmod(0o555)
        journal = (
            Path(self.ctx.runtime_dir)
            / "mount-session.json"
        )
        journal_before = journal.read_bytes()
        with self.assertRaises(
            mount_session.MountRecoveryError
        ):
            with mount_session.MountSession(self.ctx):
                pass
        self.assertEqual(journal.read_bytes(), journal_before)
        self.assertTrue(candidate.exists())
        self.assertFalse(self.iso_path.exists())

    def test_foreign_publication_namespace_fails_closed(self):
        foreign = (
            self.work / ".liveusb-publish-foreign.candidate"
        )
        foreign.write_bytes(b"foreign")
        with self.assertRaises(
            mount_session.MountRecoveryError
        ):
            with mount_session.MountSession(self.ctx) as session:
                session.begin_external_publication(
                    str(self.iso_path),
                    str(self.sidecar_path),
                    "final-image",
                )
        self.assertEqual(foreign.read_bytes(), b"foreign")

    def test_corrupt_external_journal_preserves_all_evidence(self):
        with self.assertRaises(RuntimeError):
            with mount_session.MountSession(self.ctx) as session:
                _view, candidate, _digest = self._seal_candidate(
                    session
                )
                raise RuntimeError("retain candidate")
        journal = (
            Path(self.ctx.runtime_dir)
            / "mount-session.json"
        )
        raw = b"{corrupt external journal\n"
        journal.write_bytes(raw)
        journal.chmod(0o600)

        with self.assertRaises(
            mount_session.MountRecoveryError
        ):
            with mount_session.MountSession(self.ctx):
                pass

        self.assertEqual(journal.read_bytes(), raw)
        self.assertTrue(candidate.exists())

    def test_external_path_escape_preserves_evidence(self):
        with self.assertRaises(RuntimeError):
            with mount_session.MountSession(self.ctx) as session:
                _view, candidate, _digest = self._seal_candidate(
                    session
                )
                raise RuntimeError("retain candidate")
        journal = (
            Path(self.ctx.runtime_dir)
            / "mount-session.json"
        )
        data = json.loads(journal.read_text(encoding="utf-8"))
        data["external"]["primary"]["candidate_path"] = str(
            self.root / "escaped.iso"
        )
        journal.write_text(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        journal.chmod(0o600)
        journal_before = journal.read_bytes()

        with self.assertRaises(
            mount_session.MountRecoveryError
        ):
            with mount_session.MountSession(self.ctx):
                pass

        self.assertEqual(journal.read_bytes(), journal_before)
        self.assertTrue(candidate.exists())

    def test_external_owner_mismatch_preserves_evidence(self):
        with self.assertRaises(RuntimeError):
            with mount_session.MountSession(self.ctx) as session:
                _view, candidate, _digest = self._seal_candidate(
                    session
                )
                raise RuntimeError("retain candidate")
        journal = (
            Path(self.ctx.runtime_dir)
            / "mount-session.json"
        )
        data = json.loads(journal.read_text(encoding="utf-8"))
        data["external"]["primary"]["identity"]["owner"] = (
            os.geteuid() + 1
        )
        journal.write_text(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        journal.chmod(0o600)
        journal_before = journal.read_bytes()

        with self.assertRaises(
            mount_session.MountRecoveryError
        ):
            with mount_session.MountSession(self.ctx):
                pass

        self.assertEqual(journal.read_bytes(), journal_before)
        self.assertTrue(candidate.exists())

    def test_nonregular_prior_output_fails_before_mutation(self):
        for node_type in ("directory", "symlink", "fifo"):
            with self.subTest(node_type=node_type):
                fixture = self.root / f"unsafe-{node_type}"
                fixture.mkdir()
                fixture.chmod(0o700)
                self._configure_fixture(fixture)
                if node_type == "directory":
                    self.iso_path.mkdir()
                elif node_type == "symlink":
                    target = self.work / "target"
                    target.write_bytes(b"target")
                    self.iso_path.symlink_to(target)
                else:
                    os.mkfifo(self.iso_path)
                self.sidecar_path.write_text(
                    "not valid\n",
                    encoding="utf-8",
                )

                with self.assertRaises(
                    mount_session.MountRecoveryError
                ):
                    with mount_session.MountSession(
                        self.ctx
                    ) as session:
                        session.begin_external_publication(
                            str(self.iso_path),
                            str(self.sidecar_path),
                            "final-image",
                        )

                self.assertTrue(os.path.lexists(self.iso_path))
                self.assertTrue(self.sidecar_path.exists())
                self.assertFalse(
                    any(
                        path.name.startswith(
                            ".liveusb-publish-"
                        )
                        for path in self.work.iterdir()
                    )
                )

    def test_legacy_profile_rejects_symlinked_required_nodes(self):
        real = self.iso_tree / "real-isolinux.bin"
        real.write_bytes(b"boot")
        target = self.iso_tree / "isolinux/isolinux.bin"
        target.unlink()
        target.symlink_to(real)
        self.assertFalse(rebuild._legacy_media_profile(self.ctx))

    def test_sidecar_uses_basename_not_absolute_path(self):
        digest = "a" * 64
        self.assertEqual(
            rebuild._sidecar_payload(
                digest,
                str(self.iso_path),
            ),
            (
                ("a" * 64)
                + "  Ubuntu-amd64-14.04.iso\n"
            ).encode("ascii"),
        )


if __name__ == "__main__":
    unittest.main()
