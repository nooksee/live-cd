"""Backend remastering logic, ported from .hidden/common and .hidden/scripts/*."""

import os
import subprocess
from dataclasses import dataclass, field

from .. import config
from .. import constants
from .. import messages


@dataclass
class Context:
    """Runtime settings, equivalent to the variables sourced from
    /etc/live-cd/default by the original bash scripts (now /etc/live-usb/default)."""

    work_dir: str
    mount_dir: str
    messages_colors: bool = True
    force_chroot: bool = False
    apt_helper: bool = True
    resolution: str = "1024x768"
    boot_files: bool = False
    vram: str = constants.DEFAULT_VRAM
    compression: str = "xz"
    locales: str = "C"
    iso: str = ""
    deb: str = ""
    hook: str = ""
    pic: str = ""

    @property
    def fs_dir(self):
        return os.path.join(self.work_dir, "FileSystem")

    @property
    def iso_dir(self):
        return os.path.join(self.work_dir, "ISO")

    @classmethod
    def load(cls):
        env = config.load_env()
        return cls(
            work_dir=env["WORK_DIR"],
            mount_dir=env["MOUNT_DIR"],
            messages_colors=env["MESSAGES_COLORS"] == "1",
            force_chroot=env["FORCE_CHROOT"] == "1",
            apt_helper=env["APT_HELPER"] == "1",
            resolution=env["RESOLUTION"],
            boot_files=env["BOOT_FILES"] == "1",
            vram=env["VRAM"],
            compression=env["COMPRESSION"],
            locales=env["LOCALES"],
            iso=env["ISO"],
            deb=env["DEB"],
            hook=env["HOOK"],
            pic=env["PIC"],
        )


def run(cmd, **kwargs):
    """Run *cmd* (a list of args), letting stdout/stderr passthrough."""
    messages.debug_msg("+ " + " ".join(cmd))
    return subprocess.run(cmd, **kwargs)


def run_ok(cmd, **kwargs):
    """Like run(), but returns True/False instead of raising on nonzero exit."""
    result = run(cmd, **kwargs)
    return result.returncode == 0


def capture(cmd, **kwargs):
    """Run *cmd* and return its stdout, stripped."""
    messages.debug_msg("+ " + " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **kwargs)
    return result.stdout.strip()


def is_mounted(path):
    """Equivalent of `grep "$path" /proc/mounts`."""
    try:
        with open("/proc/mounts") as fh:
            return any(path in line for line in fh)
    except OSError:
        return False
