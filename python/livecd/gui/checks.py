"""Pure (widget-free) status checks, ported from the Gambas Check module.

These are consulted by the main window to decide which widgets should be
enabled and what values to display; they don't touch GTK themselves so they
stay easy to test.
"""

import os
import subprocess

from .. import config


def x_session_available(work_dir):
    xsessions_dir = os.path.join(work_dir, "FileSystem/usr/share/xsessions")
    try:
        return len(os.listdir(xsessions_dir)) >= 1
    except OSError:
        return False


def pkg_manager_available(work_dir):
    for name in ("synaptic", "aptitude"):
        for prefix in ("bin", "sbin", "usr/bin", "usr/sbin"):
            if os.path.exists(os.path.join(work_dir, "FileSystem", prefix, name)):
                return True
    return False


def built_iso_path(work_dir):
    """Returns the path a rebuilt ISO would have if it exists, else None."""
    lsb_release = os.path.join(work_dir, "FileSystem/etc/lsb-release")
    dist = config.get_str(lsb_release, "DISTRIB_ID=", "Custom")
    release = config.get_str(lsb_release, "DISTRIB_RELEASE=", "13.10")

    fs_dir = os.path.join(work_dir, "FileSystem")
    try:
        result = subprocess.run(
            ["chroot", fs_dir, "dpkg", "--print-architecture"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        arch = result.stdout.strip()
    except OSError:
        arch = ""

    iso_path = os.path.join(work_dir, f"{dist}-{arch}-{release}.iso")
    return iso_path if os.path.exists(iso_path) else None


ESSENTIAL_STATUS_MISSING = "missing"
ESSENTIAL_STATUS_CORRUPT = "corrupt"
ESSENTIAL_STATUS_INCOMPLETE = "incomplete"
ESSENTIAL_STATUS_OK = "ok"


def essential_status(work_dir):
    """Port of Check.Existence()'s branching logic."""
    fs_dir = os.path.join(work_dir, "FileSystem")
    iso_dir = os.path.join(work_dir, "ISO")

    if not (os.path.isdir(fs_dir) and os.path.isdir(iso_dir)):
        return ESSENTIAL_STATUS_MISSING

    if not all(os.path.isdir(os.path.join(fs_dir, d)) for d in ("root", "etc", "usr")):
        return ESSENTIAL_STATUS_CORRUPT

    if not os.path.exists(os.path.join(fs_dir, "etc/casper.conf")) or not os.path.exists(
        os.path.join(fs_dir, "etc/lsb-release")
    ):
        return ESSENTIAL_STATUS_INCOMPLETE

    return ESSENTIAL_STATUS_OK
