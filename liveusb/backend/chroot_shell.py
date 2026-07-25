"""Port of .hidden/scripts/chroot: open an interactive chroot shell."""

from . import mounts, chroot
from .. import messages


def run_chroot(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.update_distro_name(ctx)
    chroot.check_sources_list()
    mounts.mount_sys(ctx)
    messages.warning("Use 'exit' to quit properly")
    chroot.chroot_run(ctx, "/bin/bash")
    mounts.umount_sys(ctx)
    mounts.recursive_umount(ctx)
