"""Port of .hidden/scripts/extract: extract a LiveUSB ISO image into the work dir."""

import os
import shutil
import subprocess
import tempfile

from . import mounts, chroot, run
from .. import messages


def _clean(ctx, mount_point=None):
    """Unmount and purge. The original bash reassigns MOUNT_DIR to the mktemp
    directory, so every __clean__ after that point unmounts the temp mount
    rather than its parent -- pass mount_point to get that behaviour."""
    target = mount_point or ctx.mount_dir
    subprocess.run(["umount", "-fl", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if mount_point is not None:
        shutil.rmtree(mount_point, ignore_errors=True)
    mounts.purge_work_dirs(ctx)


def run_extract(ctx):
    if not os.path.exists(ctx.iso):
        messages.error("The image file does not exist!")

    if os.path.isdir(ctx.fs_dir) or os.path.isdir(ctx.iso_dir):
        _clean(ctx)
        chroot.create_work_dirs(ctx)
    else:
        chroot.create_work_dirs(ctx)

    os.makedirs(ctx.mount_dir, exist_ok=True)
    mount_point = tempfile.mkdtemp(dir=ctx.mount_dir)

    messages.info("Mounting image file")
    if not run(["mount", "-t", "iso9660", "-o", "ro,loop", ctx.iso, mount_point]).returncode == 0:
        messages.error("Unable to mount image file!")

    messages.info("Checking image file")
    required = ["casper", ".disk", "isolinux"]
    ok = all(os.path.isdir(os.path.join(mount_point, d)) for d in required)
    ok = ok and os.path.exists(os.path.join(mount_point, "casper/filesystem.squashfs"))
    if not ok:
        messages.error_no_exit("This is not a usable image file!")
        _clean(ctx, mount_point)
        raise messages.LiveUSBError("not a usable image file")

    messages.info("Extracting FileSystem")
    if run(["unsquashfs", "-f", "-d", ctx.fs_dir, os.path.join(mount_point, "casper/filesystem.squashfs")]).returncode != 0:
        messages.error("Unable to extract filesystem.squashfs!")

    messages.info("Checking architecture")
    arch_result = subprocess.run(["chroot", ctx.fs_dir, "dpkg", "--print-architecture"], stdout=subprocess.PIPE, text=True)
    if arch_result.returncode != 0:
        messages.error("Unable to chroot and get architecture of root FileSystem!")
    arch = arch_result.stdout.strip()
    machine = subprocess.run(["uname", "-m"], stdout=subprocess.PIPE, text=True).stdout.strip()
    if arch == "amd64" and machine != "x86_64":
        messages.error_no_exit("The image file's architecture is amd64, yours is not!")
        _clean(ctx, mount_point)
        raise messages.LiveUSBError("architecture mismatch")

    messages.info("Copying extracted files")
    rsync_result = run([
        "rsync",
        "--exclude=/casper/*",
        "--exclude=/md5sum.txt",
        "--exclude=/README.diskdefines",
        "-a",
        mount_point + "/",
        ctx.iso_dir,
    ])
    if rsync_result.returncode != 0:
        messages.error("Unable to rsync files!")

    messages.info("Unmounting image file")
    if run(["umount", "-f", mount_point]).returncode != 0:
        messages.extra_error("Unable to unmount image file from", mount_point)
    shutil.rmtree(mount_point, ignore_errors=True)
