"""Locate icons, pictures and the CLI helper, both in a dev checkout and
once installed system-wide."""

import os
import shutil

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))          # .../liveusb
_REPO_ROOT = os.path.normpath(os.path.join(_GUI_DIR, os.pardir))        # repo root (dev checkout)

_PIXMAP_DIRS = [_REPO_ROOT, "/usr/share/live-usb", "/usr/share/pixmaps/live-usb"]
_BIN_DIRS = [os.path.join(_REPO_ROOT, "bin"), "/usr/bin", "/usr/local/bin"]


def find_pixmap(*relative_parts):
    for base in _PIXMAP_DIRS:
        path = os.path.join(base, *relative_parts)
        if os.path.exists(path):
            return path
    return None


def icon_path(name):
    """An icon under icons/, e.g. icon_path('build.png')."""
    return find_pixmap("icons", name)


def app_icon_path():
    """The application logo, live-usb.svg."""
    return find_pixmap("live-usb.svg")


def app_png_path():
    return find_pixmap("live-usb.png")


def find_cli_executable():
    for base in _BIN_DIRS:
        candidate = os.path.join(base, "live-usb")
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("live-usb")
    return found or "live-usb"
