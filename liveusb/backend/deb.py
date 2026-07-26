"""Port of .hidden/scripts/deb: install a single .deb package into the FileSystem."""

import os
import shutil

from . import mounts, chroot, mount_session
from .. import messages


def run_deb(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.check_sources_list()

    if not os.path.exists(ctx.deb):
        messages.error("Debian package does not exist!")

    if not ctx.deb.endswith(".deb"):
        messages.error("This is not a debian package!")

    shutil.copyfile(ctx.deb, os.path.join(ctx.fs_dir, "tmp/temp.deb"))

    with mount_session.MountSession(ctx) as session:
        session.allow_local_x_access()
        session.mount_sys()
        chroot.chroot_run(ctx, "dpkg", "-i", "/tmp/temp.deb")
        chroot.chroot_run(ctx, "apt-get", "install", "-f", "-y")
