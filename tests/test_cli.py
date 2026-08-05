from __future__ import annotations

import unittest
import types
from unittest import mock

from liveusb import cli
from liveusb.backend import factory_plan


class CliSafetyTests(unittest.TestCase):
    def run_without_cli_side_effects(self, argv):
        with mock.patch.object(
            cli.config,
            "load_env",
        ) as load_env, mock.patch.object(
            cli,
            "root_it",
        ) as root_it, mock.patch.object(
            cli.messages,
            "extra_error_no_exit",
        ) as report_error, mock.patch(
            "builtins.print",
        ) as print_output:
            result = cli.main(argv)

        return result, load_env, root_it, report_error, print_output

    def test_no_arguments_print_usage_without_side_effects(self) -> None:
        result, load_env, root_it, report_error, print_output = (
            self.run_without_cli_side_effects([])
        )

        self.assertEqual(result, 0)
        load_env.assert_not_called()
        root_it.assert_not_called()
        report_error.assert_not_called()
        print_output.assert_called_once_with(cli.USAGE)

    def test_help_performs_no_configuration_or_privileged_action(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                result, load_env, root_it, report_error, print_output = (
                    self.run_without_cli_side_effects([flag])
                )

                self.assertEqual(result, 0)
                load_env.assert_not_called()
                root_it.assert_not_called()
                report_error.assert_not_called()
                print_output.assert_called_once_with(cli.USAGE)

    def test_version_performs_no_configuration_or_privileged_action(self) -> None:
        for flag in ("-v", "--version"):
            with self.subTest(flag=flag):
                result, load_env, root_it, report_error, print_output = (
                    self.run_without_cli_side_effects([flag])
                )

                self.assertEqual(result, 0)
                load_env.assert_not_called()
                root_it.assert_not_called()
                report_error.assert_not_called()
                print_output.assert_called_once_with(cli.VERSION_TEXT)

    def test_unknown_argument_fails_before_all_side_effects(self) -> None:
        result, load_env, root_it, report_error, _print_output = (
            self.run_without_cli_side_effects(["--unknown"])
        )

        self.assertEqual(result, 2)
        load_env.assert_not_called()
        root_it.assert_not_called()
        report_error.assert_called_once_with(
            "unrecognised argument",
            "--unknown",
        )

    def test_valid_argument_before_unknown_still_has_no_side_effects(self) -> None:
        result, load_env, root_it, report_error, _print_output = (
            self.run_without_cli_side_effects(["--extract", "--unknown"])
        )

        self.assertEqual(result, 2)
        load_env.assert_not_called()
        root_it.assert_not_called()
        report_error.assert_called_once_with(
            "unrecognised argument",
            "--unknown",
        )

    def test_unknown_argument_before_valid_still_has_no_side_effects(self) -> None:
        result, load_env, root_it, report_error, _print_output = (
            self.run_without_cli_side_effects(["--unknown", "--extract"])
        )

        self.assertEqual(result, 2)
        load_env.assert_not_called()
        root_it.assert_not_called()
        report_error.assert_called_once_with(
            "unrecognised argument",
            "--unknown",
        )

    def test_valid_operational_flags_retain_order_and_duplicates(self) -> None:
        result, load_env, root_it, report_error, _print_output = (
            self.run_without_cli_side_effects(
                ["-q", "--extract", "-t", "--extract"]
            )
        )

        self.assertEqual(result, 0)
        load_env.assert_called_once_with()
        self.assertEqual(
            [call.args[0] for call in root_it.call_args_list],
            ["qemu", "extract", "clean", "extract"],
        )
        report_error.assert_not_called()

    def test_legacy_rebuild_is_refused_before_configuration(self) -> None:
        for argv in (("-r",), ("--extract", "--rebuild")):
            with self.subTest(argv=argv), mock.patch.object(
                cli.config,
                "load_env",
            ) as load_env, mock.patch.object(
                cli,
                "root_it",
            ) as root_it, mock.patch.object(
                cli.messages,
                "extra_error_no_exit",
            ) as report_error:
                result = cli.main(list(argv))

            self.assertEqual(result, 2)
            load_env.assert_not_called()
            root_it.assert_not_called()
            report_error.assert_called()

    def test_invalid_factory_grammar_has_zero_side_effects(self) -> None:
        with mock.patch.object(
            cli.Context,
            "load_strict",
        ) as load_strict, mock.patch.object(
            cli.factory_execution,
            "issue_complete_rebuild",
        ) as issue, mock.patch.object(
            cli.messages,
            "extra_error_no_exit",
        ) as report_error:
            result = cli.main(
                ["factory", "plan", "rebuild", "--wrong", "/tmp/records"]
            )

        self.assertEqual(result, 2)
        load_strict.assert_not_called()
        issue.assert_not_called()
        report_error.assert_called_once()

    def test_factory_plan_uses_strict_configuration_and_exact_path(self) -> None:
        context = object()
        receipt = factory_plan.FactoryReceipt(
            {
                "decision": "granted",
                "status": "issued",
            }
        )
        authorization = types.SimpleNamespace(
            factory_authority_granted=True,
            receipt=receipt,
        )
        with mock.patch.object(
            cli.Context,
            "load_strict",
            return_value=context,
        ) as load_strict, mock.patch.object(
            cli.factory_execution,
            "issue_complete_rebuild",
            return_value=(authorization, "/tmp/records/grant-abc", receipt),
        ) as issue, mock.patch("builtins.print") as output:
            result = cli.main(
                [
                    "factory",
                    "plan",
                    "rebuild",
                    "--records-dir",
                    "/tmp/records",
                ]
            )

        self.assertEqual(result, 0)
        load_strict.assert_called_once_with()
        issue.assert_called_once_with(context, "/tmp/records")
        self.assertEqual(output.call_count, 2)

    def test_factory_execute_requires_existing_root_process(self) -> None:
        with mock.patch.object(
            cli.os,
            "geteuid",
            return_value=1000,
        ), mock.patch.object(
            cli.Context,
            "load_strict",
        ) as load_strict, mock.patch.object(
            cli.factory_execution,
            "execute_issued_rebuild",
        ) as execute:
            result = cli.main(
                [
                    "factory",
                    "execute",
                    "rebuild",
                    "--grant",
                    "/tmp/records/grant-abc",
                ]
            )

        self.assertEqual(result, 2)
        load_strict.assert_not_called()
        execute.assert_not_called()

    def test_factory_execute_root_path_returns_persisted_status(self) -> None:
        context = object()
        receipt = factory_plan.FactoryReceipt(
            {
                "status": "succeeded",
            }
        )
        authorization = types.SimpleNamespace(
            factory_authority_granted=True,
        )
        with mock.patch.object(
            cli.os,
            "geteuid",
            return_value=0,
        ), mock.patch.object(
            cli.Context,
            "load_strict",
            return_value=context,
        ), mock.patch.object(
            cli.factory_execution,
            "execute_issued_rebuild",
            return_value=(authorization, "/tmp/records/grant-abc", receipt),
        ) as execute, mock.patch("builtins.print") as output:
            result = cli.main(
                [
                    "factory",
                    "execute",
                    "rebuild",
                    "--grant",
                    "/tmp/records/grant-abc",
                ]
            )

        self.assertEqual(result, 0)
        execute.assert_called_once_with(
            context,
            "/tmp/records/grant-abc",
        )
        self.assertEqual(output.call_count, 2)


if __name__ == "__main__":
    unittest.main()
