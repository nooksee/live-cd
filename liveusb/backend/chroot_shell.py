"""Port of .hidden/scripts/chroot: open an interactive chroot shell."""

from . import mounts, chroot, mount_session
from .. import messages


def run_chroot(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.update_distro_name(ctx)
    chroot.check_sources_list()
    with mount_session.MountSession(ctx) as session:
        session.mount_sys()
        messages.warning("Use 'exit' to quit properly")
        chroot.chroot_run(ctx, "/bin/bash")
