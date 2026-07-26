"""Port of .hidden/scripts/hook: run a user-supplied hook script inside the chroot."""

import os

from . import mounts, chroot, mount_session
from .. import messages


def run_hook(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.check_sources_list()

    if not os.path.exists(ctx.hook):
        messages.error("The hook file does not exist!")

    with mount_session.MountSession(ctx) as session:
        staged_hook = session.stage_file(
            ctx.hook,
            "hook",
            executable=True,
        )
        session.allow_local_x_access()
        session.mount_sys()
        session.mount_dbus()
        chroot.chroot_run(ctx, staged_hook)
