"""Port of .hidden/scripts/qemu: boot the rebuilt ISO in a desktop emulator."""

import os
import platform
import subprocess

from . import mounts, run
from .. import messages


def qemu_command(executable, iso_path, vram):
    """Return the accepted BIOS CD-ROM QEMU argv."""

    return executable, "-cdrom", iso_path, "-m", str(vram)


def run_qemu(ctx):
    mounts.check_for_x()

    messages.info("Retrieving distribution info")
    arch_result = subprocess.run(["chroot", ctx.fs_dir, "dpkg", "--print-architecture"], stdout=subprocess.PIPE, text=True)
    if arch_result.returncode != 0:
        messages.error("Unable to chroot and get architecture of distribution FileSystem!")
    arch = arch_result.stdout.strip()

    dist = _grep_value(os.path.join(ctx.fs_dir, "etc/lsb-release"), "DISTRIB_ID=")
    version = _grep_value(os.path.join(ctx.fs_dir, "etc/lsb-release"), "DISTRIB_RELEASE=")
    if dist is None:
        messages.error("Unable to get distribution ID!")
    if version is None:
        messages.error("Unable to get distribution version!")

    iso_path = os.path.join(ctx.work_dir, f"{dist}-{arch}-{version}.iso")
    if not os.path.exists(iso_path):
        messages.extra_error("Image file does not exist!", iso_path)

    messages.extra_info("Running desktop emulator with image file", iso_path)
    qemu_bin = "qemu-system-x86_64" if platform.machine() == "x86_64" else "qemu-system-i386"
    if run(list(qemu_command(qemu_bin, iso_path, ctx.vram))).returncode != 0:
        messages.error("An error occurred while loading image file!")


def _grep_value(path, key):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith(key):
                    return line[len(key):].strip().strip('"')
    except OSError:
        return None
    return None
