"""Locate icons, pictures and the CLI helper, both in a dev checkout and
once installed system-wide."""

import os
import shutil

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))          # .../python/livecd
_PYTHON_DIR = os.path.normpath(os.path.join(_GUI_DIR, os.pardir))       # .../python
_REPO_ROOT = os.path.normpath(os.path.join(_PYTHON_DIR, os.pardir))     # repo root (dev checkout)

_PIXMAP_DIRS = [_REPO_ROOT, "/usr/share/live-cd", "/usr/share/pixmaps/live-cd"]
_BIN_DIRS = [os.path.join(_PYTHON_DIR, "bin"), "/usr/bin", "/usr/local/bin"]


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
    """The application logo, live-cd.svg."""
    return find_pixmap("live-cd.svg")


def app_png_path():
    return find_pixmap("live-cd.png")


def find_cli_executable():
    for base in _BIN_DIRS:
        candidate = os.path.join(base, "live-cd")
        if os.path.exists(candidate):
            return candidate
    found = shutil.which("live-cd")
    return found or "live-cd"
