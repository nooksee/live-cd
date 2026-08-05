from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import preflight
from liveusb.backend import preflight_runtime


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
d---------   0    0    0            2048 Jul 30 2026 [     20 02]  .disk
d---------   0    0    0            2048 Jul 30 2026 [     21 02]  casper
d---------   0    0    0            2048 Jul 30 2026 [     22 02]  isolinux
Directory listing of /isolinux/
----------   0    0    0              16 Jul 30 2026 [     23 00]  isolinux.bin
"""

ISOINFO_REAL_FORMAT_LISTING = (
    "Directory listing of /\n"
    "drwxrwxr-x   5 1750 1750            2048 Jul 30 2026 "
    "[     23 02]  .\x20\n"
    "drwxrwxr-x   5 1750 1750            2048 Jul 30 2026 "
    "[     23 02]  ..\x20\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     26 02]  casper\x20\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     25 02]  isolinux\x20\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     24 02]  .disk\x20\n"
    "Directory listing of /casper/\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     26 02]  .\x20\n"
    "drwxrwxr-x   5 1750 1750            2048 Jul 30 2026 "
    "[     23 02]  ..\x20\n"
    "Directory listing of /isolinux/\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     25 02]  .\x20\n"
    "drwxrwxr-x   5 1750 1750            2048 Jul 30 2026 "
    "[     23 02]  ..\x20\n"
    "-rw-rw-r--   1 1750 1750              34 Jul 30 2026 "
    "[     28 00]  isolinux.bin\x20\n"
    "Directory listing of /.disk/\n"
    "drwxrwxr-x   2 1750 1750            2048 Jul 30 2026 "
    "[     24 02]  .\x20\n"
    "drwxrwxr-x   5 1750 1750            2048 Jul 30 2026 "
    "[     23 02]  ..\x20\n"
)

XORRISO_LEGACY_LISTING = """\
dr-xr-xr-x    1 0        0               0 Jul 30 00:00 '/isolinux'
-r--r--r--    1 0        0              16 Jul 30 00:00 '/isolinux/isolinux.bin'
dr-xr-xr-x    1 0        0               0 Jul 30 00:00 '/casper'
dr-xr-xr-x    1 0        0               0 Jul 30 00:00 '/.disk'
"""

ISOHYBRID_REAL_VERSION_OUTPUT = "/usr/bin/isohybrid version 0.12\n"


class RecordingExecutor:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((tuple(command), dict(options)))
        return self.callback(tuple(command), dict(options))


class RuntimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(mode=0o700)
        self.source = self.root / "source.iso"
        self.source.write_bytes(b"synthetic source media")

    def add_executable(self, name, payload=b"synthetic executable\n"):
        path = self.bin_dir / name
        path.write_bytes(payload)
        path.chmod(0o700)
        return path

    def resolver(self, name):
        path = self.bin_dir / name
        return str(path) if path.exists() else None

    def source_finding(self):
        return preflight.PreflightEngine()._inspect_source_iso(
            str(self.source)
        )

    def version_executor(self, command, _options):
        name = os.path.basename(command[0])
        return preflight_runtime.CommandOutcome(
            0,
            stdout=VERSION_OUTPUTS[name].encode("ascii"),
        )

    def test_version_whitelist_and_exact_query_forms(self):
        for tool in preflight_runtime.VERSION_TOOL_ORDER:
            self.add_executable(tool)
        recorder = RecordingExecutor(self.version_executor)
        engine = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=recorder,
        )

        results = engine.query_all_versions()

        expected_arguments = {
            spec.tool: spec.arguments
            for spec in preflight_runtime.VERSION_QUERY_SPECS
        }
        self.assertEqual(
            tuple(result.probe_id for result in results),
            tuple(
                "version." + tool
                for tool in preflight_runtime.VERSION_TOOL_ORDER
            ),
        )
        self.assertTrue(
            all(
                result.status == preflight_runtime.STATUS_SUCCESS
                for result in results
            )
        )
        self.assertEqual(len(recorder.calls), 10)
        for result, (command, options) in zip(results, recorder.calls):
            tool = result.probe_id[len("version."):]
            self.assertTrue(os.path.isabs(command[0]))
            self.assertEqual(command[1:], expected_arguments[tool])
            self.assertEqual(options["pass_fds"], ())
            self.assertEqual(options["environment"]["LC_ALL"], "C")
            self.assertEqual(options["environment"]["LANGUAGE"], "C")
            self.assertFalse(result.to_dict()["factory_authority_granted"])

    def test_unknown_version_tool_is_rejected_before_discovery(self):
        resolver = mock.Mock()
        executor = mock.Mock()
        engine = preflight_runtime.RuntimeEvidenceEngine(
            resolver=resolver,
            executor=executor,
        )

        with self.assertRaisesRegex(ValueError, "whitelist"):
            engine.query_version("unknown-tool")

        resolver.assert_not_called()
        executor.assert_not_called()

    def test_absent_tool_is_explicit_and_executes_nothing(self):
        executor = mock.Mock()
        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=lambda _name: None,
            executor=executor,
        ).query_version("isohybrid")

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_ABSENT,
        )
        self.assertEqual(result.command, ())
        self.assertFalse(result.evidence["discovered"])
        executor.assert_not_called()

    def test_nonfinite_timeout_rejected_before_process_factory(self):
        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                process_factory = mock.Mock()

                result = preflight_runtime._bounded_execute(
                    ("/absolute/probe",),
                    timeout_seconds=timeout,
                    output_limit_bytes=1024,
                    popen=process_factory,
                )

                self.assertEqual(
                    result.error_type,
                    "InvalidCommandContract",
                )
                process_factory.assert_not_called()

    def test_engine_rejects_nonfinite_timeout_before_probe(self):
        for timeout in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(timeout=timeout):
                resolver = mock.Mock()
                executor = mock.Mock()

                with self.assertRaisesRegex(ValueError, "finite"):
                    preflight_runtime.RuntimeEvidenceEngine(
                        resolver=resolver,
                        executor=executor,
                        timeout_seconds=timeout,
                    )

                resolver.assert_not_called()
                executor.assert_not_called()

    def test_default_resolver_uses_fixed_probe_path(self):
        executor = mock.Mock()
        with mock.patch.object(
            preflight_runtime.shutil,
            "which",
            return_value=None,
        ) as which:
            result = preflight_runtime.RuntimeEvidenceEngine(
                executor=executor,
            ).query_version("isohybrid")

        self.assertEqual(result.status, preflight_runtime.STATUS_ABSENT)
        which.assert_called_once_with(
            "isohybrid",
            path=preflight_runtime._PROBE_ENVIRONMENT["PATH"],
        )
        executor.assert_not_called()

    def test_fixed_probe_path_includes_system_administration_tools(self):
        self.assertEqual(
            tuple(
                preflight_runtime._PROBE_ENVIRONMENT["PATH"].split(
                    os.pathsep
                )
            ),
            preflight_runtime._TRUSTED_TOOL_DIRECTORIES,
        )
        self.assertEqual(
            preflight_runtime._TRUSTED_TOOL_DIRECTORIES,
            (
                "/usr/sbin",
                "/usr/bin",
                "/sbin",
                "/bin",
            ),
        )

    def test_ambient_only_tool_is_not_selected(self):
        fake = self.add_executable("isohybrid")
        executor = mock.Mock(
            return_value=preflight_runtime.CommandOutcome(
                0,
                stdout=VERSION_OUTPUTS["isohybrid"].encode("ascii"),
            )
        )

        with mock.patch.dict(os.environ, {"PATH": str(self.bin_dir)}):
            result = preflight_runtime.RuntimeEvidenceEngine(
                executor=executor,
            ).query_version("isohybrid")

        self.assertNotIn(str(fake), result.command)
        for call in executor.call_args_list:
            self.assertNotEqual(call.args[0][0], str(fake))

    def test_nonzero_timeout_malformed_and_execution_error_are_distinct(self):
        self.add_executable("mksquashfs")
        cases = (
            (
                preflight_runtime.CommandOutcome(
                    3,
                    stderr=b"unexpected option\n",
                ),
                preflight_runtime.STATUS_NONZERO,
            ),
            (
                preflight_runtime.CommandOutcome(
                    -15,
                    timed_out=True,
                ),
                preflight_runtime.STATUS_TIMEOUT,
            ),
            (
                preflight_runtime.CommandOutcome(
                    0,
                    stdout=b"unrecognized success text\n",
                ),
                preflight_runtime.STATUS_MALFORMED,
            ),
            (
                preflight_runtime.CommandOutcome(
                    None,
                    error_type="OSError",
                ),
                preflight_runtime.STATUS_EXECUTION_ERROR,
            ),
            (
                preflight_runtime.CommandOutcome(
                    -15,
                    stdout=b"x" * 32,
                    output_limited=True,
                ),
                preflight_runtime.STATUS_MALFORMED,
            ),
        )
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                engine = preflight_runtime.RuntimeEvidenceEngine(
                    resolver=self.resolver,
                    executor=lambda _command, **_options: outcome,
                )
                result = engine.query_version("mksquashfs")
                self.assertEqual(result.status, expected)
                if expected == preflight_runtime.STATUS_NONZERO:
                    self.assertFalse(
                        result.evidence["version_output_matched"]
                    )
                    self.assertNotIn("version_line", result.evidence)

    def test_nonzero_matching_version_is_retained_as_secondary_evidence(self):
        self.add_executable("unsquashfs")
        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=lambda _command, **_options: (
                preflight_runtime.CommandOutcome(
                    1,
                    stdout=VERSION_OUTPUTS["unsquashfs"].encode("ascii"),
                )
            ),
        ).query_version("unsquashfs")

        self.assertEqual(result.status, preflight_runtime.STATUS_NONZERO)
        self.assertTrue(result.evidence["version_output_matched"])
        self.assertEqual(
            result.evidence["version_line"],
            VERSION_OUTPUTS["unsquashfs"].strip(),
        )
        self.assertFalse(result.to_dict()["factory_authority_granted"])

    def test_real_isohybrid_version_output_accepts_absolute_argv0(self):
        self.add_executable("isohybrid")
        executor = mock.Mock(
            return_value=preflight_runtime.CommandOutcome(
                0,
                stdout=ISOHYBRID_REAL_VERSION_OUTPUT.encode("ascii"),
                termination_confirmed=True,
            )
        )

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=executor,
        ).query_version("isohybrid")

        self.assertEqual(result.status, preflight_runtime.STATUS_SUCCESS)
        self.assertEqual(
            result.evidence["version_line"],
            ISOHYBRID_REAL_VERSION_OUTPUT.strip(),
        )
        self.assertTrue(result.evidence["version_output_matched"])
        self.assertFalse(result.to_dict()["factory_authority_granted"])

    def test_success_with_version_on_stderr_is_preserved(self):
        self.add_executable("xorriso")
        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=lambda _command, **_options: (
                preflight_runtime.CommandOutcome(
                    0,
                    stderr=VERSION_OUTPUTS["xorriso"].encode("ascii"),
                )
            ),
        ).query_version("xorriso")

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertIn("xorriso", result.evidence["version_line"])

    def test_resolved_symlink_executes_canonical_absolute_target(self):
        target = self.add_executable("real-mount")
        alias = self.bin_dir / "mount"
        alias.symlink_to(target.name)
        recorder = RecordingExecutor(
            lambda _command, _options: (
                preflight_runtime.CommandOutcome(
                    0,
                    stdout=VERSION_OUTPUTS["mount"].encode("ascii"),
                )
            )
        )

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=recorder,
        ).query_version("mount")

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertEqual(recorder.calls[0][0][0], str(target))
        self.assertEqual(result.command[0], str(target))

    def test_default_executor_enforces_process_contract(self):
        helper = self.bin_dir / "mksquashfs"
        helper.write_text(
            "#!/usr/bin/python3\n"
            "import os\n"
            "good = (\n"
            "    os.environ.get('LC_ALL') == 'C'\n"
            "    and os.environ.get('LANGUAGE') == 'C'\n"
            "    and os.read(0, 1) == b''\n"
            ")\n"
            "print('mksquashfs version 4.6.1' if good else 'bad')\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        calls = []

        def recording_popen(*arguments, **options):
            calls.append((arguments, options))
            return subprocess.Popen(*arguments, **options)

        def executor(command, **options):
            return preflight_runtime._bounded_execute(
                command,
                popen=recording_popen,
                **options,
            )

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=executor,
        ).query_version("mksquashfs")

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertEqual(len(calls), 1)
        _arguments, options = calls[0]
        self.assertIs(options["shell"], False)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.PIPE)
        self.assertEqual(options["stderr"], subprocess.PIPE)
        self.assertTrue(options["close_fds"])
        self.assertTrue(options["start_new_session"])
        self.assertEqual(options["env"]["LC_ALL"], "C")
        self.assertEqual(options["env"]["LANGUAGE"], "C")

    def test_default_executor_reports_unconfirmed_process_termination(self):
        class ResistantProcess:
            def __init__(self):
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminate_calls = 0
                self.kill_calls = 0

            @staticmethod
            def poll():
                return None

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("resistant", timeout)

            def terminate(self):
                self.terminate_calls += 1

            def kill(self):
                self.kill_calls += 1

        process = ResistantProcess()
        ticks = iter((0.0, 1.0, 2.0, 3.0))
        outcome = preflight_runtime._bounded_execute(
            ("/absolute/probe",),
            timeout_seconds=0.01,
            output_limit_bytes=1024,
            popen=lambda *_args, **_kwargs: process,
            monotonic=lambda: next(ticks),
        )

        self.assertFalse(outcome.termination_confirmed)
        self.assertEqual(outcome.error_type, "ProcessDidNotStop")
        self.assertTrue(outcome.timed_out)
        self.assertGreaterEqual(process.terminate_calls, 1)
        self.assertGreaterEqual(process.kill_calls, 1)

    def test_default_executor_bounds_timeout_and_output(self):
        sleeper = self.bin_dir / "sleeper"
        sleeper.write_text(
            "#!/usr/bin/python3\n"
            "import time\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        sleeper.chmod(0o700)
        started = time.monotonic()
        timeout = preflight_runtime._bounded_execute(
            (str(sleeper),),
            timeout_seconds=0.05,
            output_limit_bytes=1024,
        )
        elapsed = time.monotonic() - started

        self.assertTrue(timeout.timed_out)
        self.assertLess(elapsed, 2.0)

        writer = self.bin_dir / "writer"
        writer.write_text(
            "#!/usr/bin/python3\n"
            "import os\n"
            "os.write(1, bytes([120]) * 65536)\n",
            encoding="utf-8",
        )
        writer.chmod(0o700)
        limited = preflight_runtime._bounded_execute(
            (str(writer),),
            timeout_seconds=2.0,
            output_limit_bytes=128,
        )

        self.assertTrue(limited.output_limited)
        self.assertLessEqual(
            len(limited.stdout) + len(limited.stderr),
            128,
        )

    def test_isoinfo_is_preferred_and_descriptor_bound(self):
        self.add_executable("isoinfo")
        self.add_executable("xorriso")
        recorder = RecordingExecutor(
            lambda _command, _options: (
                preflight_runtime.CommandOutcome(
                    0,
                    stdout=ISOINFO_LEGACY_LISTING.encode("ascii"),
                )
            )
        )
        source_finding = self.source_finding()

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=recorder,
        ).inspect_source_media(source_finding)

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertEqual(
            result.evidence["selected_provider"],
            "isoinfo",
        )
        self.assertEqual(len(recorder.calls), 1)
        command, options = recorder.calls[0]
        self.assertEqual(command[1:4], ("-R", "-l", "-i"))
        self.assertRegex(command[4], r"^/proc/self/fd/\d+$")
        self.assertEqual(len(options["pass_fds"]), 1)
        self.assertTrue(result.evidence["profile"]["recognized"])
        self.assertEqual(
            result.evidence["source_sha256_before"],
            result.evidence["source_sha256_after"],
        )

    def test_real_isoinfo_display_spacing_recognizes_legacy_profile(self):
        records = preflight_runtime._parse_isoinfo_listing(
            ISOINFO_REAL_FORMAT_LISTING
        )

        self.assertEqual(
            preflight_runtime._profile_evidence(records),
            {
                "issues": [],
                "profile": "legacy-isolinux-single-filesystem-source-media",
                "recognized": True,
                "required_nodes": {
                    "/.disk": "directory",
                    "/casper": "directory",
                    "/isolinux": "directory",
                    "/isolinux/isolinux.bin": "file",
                },
            },
        )

    def test_isoinfo_trailing_space_name_ambiguity_is_rejected(self):
        ambiguous = ISOINFO_REAL_FORMAT_LISTING.replace(
            "isolinux.bin \n",
            "isolinux.bin  \n",
        )

        with self.assertRaisesRegex(ValueError, "trailing-space"):
            preflight_runtime._parse_isoinfo_listing(ambiguous)

    def test_xorriso_fallback_and_explicit_provider_are_supported(self):
        xorriso = self.add_executable("xorriso")
        recorder = RecordingExecutor(
            lambda _command, _options: (
                preflight_runtime.CommandOutcome(
                    0,
                    stdout=XORRISO_LEGACY_LISTING.encode("ascii"),
                )
            )
        )
        engine = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=recorder,
        )

        fallback = engine.inspect_source_media(self.source_finding())
        explicit = engine.inspect_source_media(
            self.source_finding(),
            provider="xorriso",
        )

        self.assertEqual(
            fallback.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertEqual(
            explicit.status,
            preflight_runtime.STATUS_SUCCESS,
        )
        self.assertEqual(fallback.command[0], str(xorriso))
        self.assertIn("-no_rc", fallback.command)
        self.assertIn("-lsdl", fallback.command)

    def test_missing_inspectors_are_explicit_and_execute_nothing(self):
        executor = mock.Mock()
        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=lambda _name: None,
            executor=executor,
        ).inspect_source_media(self.source_finding())

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_ABSENT,
        )
        executor.assert_not_called()

    def test_partial_and_wrong_type_profiles_are_rejected(self):
        self.add_executable("isoinfo")
        partial = ISOINFO_LEGACY_LISTING.replace(
            "d---------   0    0    0            2048 Jul 30 2026 "
            "[     21 02]  casper\n",
            "",
        )
        wrong_type = ISOINFO_LEGACY_LISTING.replace(
            "----------   0    0    0              16 Jul 30 2026 "
            "[     23 00]  isolinux.bin",
            "d---------   0    0    0              16 Jul 30 2026 "
            "[     23 00]  isolinux.bin",
        )
        for listing in (partial, wrong_type):
            with self.subTest(listing=listing):
                result = preflight_runtime.RuntimeEvidenceEngine(
                    resolver=self.resolver,
                    executor=lambda _command, **_options: (
                        preflight_runtime.CommandOutcome(
                            0,
                            stdout=listing.encode("ascii"),
                        )
                    ),
                ).inspect_source_media(self.source_finding())
                self.assertEqual(
                    result.status,
                    preflight_runtime.STATUS_PROFILE_REJECTED,
                )
                self.assertFalse(
                    result.evidence["profile"]["recognized"]
                )

    def test_malformed_and_conflicting_inspector_output_fails_closed(self):
        self.add_executable("isoinfo")
        malformed = ISOINFO_LEGACY_LISTING + "unknown record\n"
        conflict = (
            ISOINFO_LEGACY_LISTING
            + "Directory listing of /\n"
            + "----------   0    0    0  16 Jul 30 2026 "
            + "[     24 00]  casper\n"
        )
        for listing in (malformed, conflict):
            with self.subTest(listing=listing):
                result = preflight_runtime.RuntimeEvidenceEngine(
                    resolver=self.resolver,
                    executor=lambda _command, **_options: (
                        preflight_runtime.CommandOutcome(
                            0,
                            stdout=listing.encode("ascii"),
                        )
                    ),
                ).inspect_source_media(self.source_finding())
                self.assertEqual(
                    result.status,
                    preflight_runtime.STATUS_MALFORMED,
                )

    def test_inspector_nonzero_timeout_and_output_limit_remain_distinct(self):
        self.add_executable("isoinfo")
        cases = (
            (
                preflight_runtime.CommandOutcome(5, stderr=b"failure\n"),
                preflight_runtime.STATUS_NONZERO,
            ),
            (
                preflight_runtime.CommandOutcome(-15, timed_out=True),
                preflight_runtime.STATUS_TIMEOUT,
            ),
            (
                preflight_runtime.CommandOutcome(
                    -15,
                    stdout=b"x" * 16,
                    output_limited=True,
                ),
                preflight_runtime.STATUS_MALFORMED,
            ),
        )
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                result = preflight_runtime.RuntimeEvidenceEngine(
                    resolver=self.resolver,
                    executor=lambda _command, **_options: outcome,
                ).inspect_source_media(self.source_finding())
                self.assertEqual(result.status, expected)

    def test_source_mutation_during_inspection_is_custody_failure(self):
        self.add_executable("isoinfo")

        def mutate(_command, _options):
            self.source.write_bytes(b"mutated source media")
            return preflight_runtime.CommandOutcome(
                0,
                stdout=ISOINFO_LEGACY_LISTING.encode("ascii"),
            )

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=RecordingExecutor(mutate),
        ).inspect_source_media(self.source_finding())

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_CUSTODY_FAILURE,
        )
        self.assertNotIn("profile", result.evidence)

    def test_source_replacement_during_inspection_is_custody_failure(self):
        self.add_executable("isoinfo")

        def replace(_command, _options):
            replacement = self.root / "replacement.iso"
            replacement.write_bytes(self.source.read_bytes())
            os.replace(replacement, self.source)
            return preflight_runtime.CommandOutcome(
                0,
                stdout=ISOINFO_LEGACY_LISTING.encode("ascii"),
            )

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=RecordingExecutor(replace),
        ).inspect_source_media(self.source_finding())

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_CUSTODY_FAILURE,
        )

    def test_hard_link_added_after_phase_a_fails_before_inspection(self):
        self.add_executable("isoinfo")
        source_finding = self.source_finding()
        original_bytes = self.source.read_bytes()
        secondary = self.root / "source-secondary.iso"
        os.link(self.source, secondary)
        executor = mock.Mock()

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=executor,
        ).inspect_source_media(source_finding)

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_CUSTODY_FAILURE,
        )
        executor.assert_not_called()
        self.assertTrue(self.source.exists())
        self.assertTrue(secondary.exists())
        self.assertTrue(os.path.samefile(self.source, secondary))
        self.assertEqual(self.source.stat().st_nlink, 2)
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(secondary.read_bytes(), original_bytes)

    def test_invalid_phase_1e_a_evidence_executes_nothing(self):
        self.add_executable("isoinfo")
        executor = mock.Mock()
        source_finding = self.source_finding().to_dict()
        source_finding["evidence"]["sha256"] = "0" * 64

        result = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=executor,
        ).inspect_source_media(source_finding)

        self.assertEqual(
            result.status,
            preflight_runtime.STATUS_CUSTODY_FAILURE,
        )
        executor.assert_not_called()

    def test_runtime_evidence_is_deterministic_sanitized_and_non_authorizing(self):
        for tool in preflight_runtime.VERSION_TOOL_ORDER:
            self.add_executable(tool)
        self.add_executable("isoinfo")

        def execute(command, _options):
            name = os.path.basename(command[0])
            if name == "isoinfo":
                return preflight_runtime.CommandOutcome(
                    0,
                    stdout=ISOINFO_LEGACY_LISTING.encode("ascii"),
                    stderr=b"token=private-value\n",
                )
            return preflight_runtime.CommandOutcome(
                0,
                stdout=VERSION_OUTPUTS[name].encode("ascii"),
            )

        evidence = preflight_runtime.RuntimeEvidenceEngine(
            resolver=self.resolver,
            executor=RecordingExecutor(execute),
        ).collect(self.source_finding())

        first = evidence.to_json()
        second = evidence.to_json()
        decoded = json.loads(first)
        self.assertEqual(first, second)
        self.assertFalse(decoded["factory_authority_granted"])
        self.assertNotIn("overall", decoded)
        self.assertNotIn("private-value", first)
        self.assertIn("<redacted>", first)
        self.assertEqual(len(decoded["version_queries"]), 10)
        self.assertEqual(decoded["counts"]["success"], 11)

    def test_parsers_reject_path_damage_and_unknown_xorriso_records(self):
        damaged = ISOINFO_LEGACY_LISTING.replace(
            "Directory listing of /isolinux/",
            "Directory listing of /isolinux/../casper/",
        )
        with self.assertRaises(ValueError):
            preflight_runtime._parse_isoinfo_listing(damaged)
        with self.assertRaises(ValueError):
            preflight_runtime._parse_xorriso_listing(
                XORRISO_LEGACY_LISTING + "not a listing record\n"
            )

    def test_source_finding_digest_matches_fixture_bytes(self):
        finding = self.source_finding()
        self.assertEqual(finding.status, preflight.STATUS_PASS)
        self.assertEqual(
            finding.evidence["sha256"],
            hashlib.sha256(self.source.read_bytes()).hexdigest(),
        )

    def test_module_has_no_factory_or_cli_integration_surface(self):
        source_path = Path(preflight_runtime.__file__)
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn("factory_authority_granted\": True", source)
        self.assertNotIn("liveusb.cli", source)
        self.assertNotIn("run_qemu", source)
        self.assertNotIn("run_extract", source)
        self.assertNotIn("run_rebuild", source)
        self.assertNotIn("sudo", source)
        self.assertEqual(
            set(preflight_runtime.VERSION_TOOL_ORDER),
            set(VERSION_OUTPUTS),
        )


if __name__ == "__main__":
    unittest.main()
