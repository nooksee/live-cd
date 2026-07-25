"""Port of .hidden/scripts/pkgm: run a package manager (synaptic/aptitude) inside the chroot."""

import os

from . import mounts, chroot
from .. import messages


def _find_pkgm(ctx):
    for name in ("synaptic", "aptitude"):
        for prefix in ("usr/sbin", "usr/bin"):
            path = os.path.join(ctx.fs_dir, prefix, name)
            if os.access(path, os.X_OK):
                return name
    return None


def run_pkgm(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.update_distro_name(ctx)
    chroot.check_sources_list()

    messages.info("Searching for package manager")
    pkgm = _find_pkgm(ctx)
    if pkgm is None:
        messages.error("No supported package managers were detected!")
    messages.extra_info("Will run", pkgm)

    mounts.allow_local_x_access()
    mounts.mount_sys(ctx)
    mounts.mount_dbus(ctx)
    chroot.chroot_run(ctx, "apt-get", "install", "dbus", "-y", "-f")
    chroot.chroot_run(ctx, "dbus-uuidgen", "--ensure")
    chroot.chroot_run(ctx, pkgm)
    mounts.umount_sys(ctx)
    mounts.recursive_umount(ctx)
    mounts.block_local_x_access()
