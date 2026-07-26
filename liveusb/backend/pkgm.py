"""Port of .hidden/scripts/pkgm: run a package manager (synaptic/aptitude) inside the chroot."""

import os

from . import mounts, chroot, mount_session
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

    with mount_session.MountSession(ctx) as session:
        session.allow_local_x_access()
        session.mount_sys()
        session.mount_dbus()
        chroot.chroot_run(
            ctx,
            "apt-get",
            "install",
            "dbus",
            "-y",
            "-f",
        )
        chroot.chroot_run(ctx, "dbus-uuidgen", "--ensure")
        chroot.chroot_run(ctx, pkgm)
