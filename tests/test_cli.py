from __future__ import annotations

import unittest
from unittest import mock

from liveusb import cli


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


if __name__ == "__main__":
    unittest.main()
