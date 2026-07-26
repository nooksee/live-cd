from __future__ import annotations

import unittest
from unittest import mock

from liveusb import constants
from liveusb.backend import Context

try:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk as _Gtk
except (ImportError, ValueError):
    settings_window = None
else:
    from liveusb.gui import settings_window


class DefaultFidelityTests(unittest.TestCase):
    def test_canonical_vram_default_is_2048(self) -> None:
        self.assertEqual(constants.DEFAULT_VRAM, "2048")

    def test_default_configuration_uses_canonical_vram(self) -> None:
        vram_lines = [
            line
            for line in constants.DEFAULT_CONFIG_CONTENT.splitlines()
            if line.startswith("VRAM=")
        ]

        self.assertEqual(
            vram_lines,
            [f"VRAM={constants.DEFAULT_VRAM}"],
        )

    def test_backend_context_uses_canonical_vram_default(self) -> None:
        context = Context(
            work_dir="/temporary/work",
            mount_dir="/temporary/mount",
        )

        self.assertEqual(context.vram, constants.DEFAULT_VRAM)

    @unittest.skipIf(settings_window is None, "PyGObject is not installed")
    def test_gui_uses_canonical_vram_fallback_without_launching(self) -> None:
        with mock.patch.object(
            settings_window.config,
            "get_config_str",
            side_effect=lambda _key, default: default,
        ) as get_config_str:
            result = settings_window._get_vram_setting()

        self.assertEqual(result, constants.DEFAULT_VRAM)
        self.assertIn(constants.DEFAULT_VRAM, settings_window.VRAM_SIZES)
        get_config_str.assert_called_once_with(
            "VRAM=",
            constants.DEFAULT_VRAM,
        )


if __name__ == "__main__":
    unittest.main()
