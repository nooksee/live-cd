from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveusb import config


class ConfigCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        debug_patcher = mock.patch.object(config.messages, "debug_msg")
        debug_patcher.start()
        self.addCleanup(debug_patcher.stop)

    def test_double_quote_round_trip(self) -> None:
        values = (
            "plain",
            "value with spaces",
            r"C:\work\live-usb",
            'say "hello"',
        )

        for value in values:
            with self.subTest(value=value):
                self.assertEqual(config._unquote(config._quote(value)), value)

    def test_unquote_handles_single_quotes_and_bare_values(self) -> None:
        self.assertEqual(config._unquote("'single quoted'"), "single quoted")
        self.assertEqual(config._unquote("  bare value  "), "bare value")

    def test_get_str_reads_quoted_and_unquoted_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default"
            path.write_text(
                'QUOTED="value with spaces"\nBARE=plain\n',
                encoding="utf-8",
            )

            self.assertEqual(
                config.get_str(str(path), "QUOTED=", "unused"),
                "value with spaces",
            )
            self.assertEqual(
                config.get_str(str(path), "BARE=", "unused"),
                "plain",
            )

    def test_get_str_appends_a_quoted_default_when_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default"
            path.write_text("PRESENT=1", encoding="utf-8")

            result = config.get_str(str(path), "MISSING=", "fallback value")

            self.assertEqual(result, "fallback value")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                'PRESENT=1\nMISSING="fallback value"',
            )

    def test_replace_str_quotes_replacement_and_preserves_other_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default"
            path.write_text("TARGET=old\nKEEP=plain\n", encoding="utf-8")

            config.replace_str(str(path), "TARGET=", 'new "value"')

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                'TARGET="new \\"value\\""\nKEEP=plain\n',
            )
            self.assertEqual(
                config.get_str(str(path), "TARGET=", "unused"),
                'new "value"',
            )

    def test_replace_str_appends_a_quoted_value_when_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default"
            path.write_text("PRESENT=1", encoding="utf-8")

            config.replace_str(str(path), "ADDED=", "new value")

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                'PRESENT=1\nADDED="new value"',
            )

    def test_replace_str_as_is_does_not_add_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "default"
            path.write_text("TARGET=old\n", encoding="utf-8")

            config.replace_str_as_is(str(path), "TARGET=", "raw value")

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "TARGET=raw value\n",
            )


if __name__ == "__main__":
    unittest.main()
