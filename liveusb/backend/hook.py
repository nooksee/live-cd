"""Port of .hidden/scripts/hook: run a user-supplied hook script inside the chroot."""

import os
import shutil
import stat

from . import mounts, chroot, mount_session
from .. import messages


def run_hook(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.check_sources_list()

    if not os.path.exists(ctx.hook):
        messages.error("The hook file does not exist!")

    target = os.path.join(ctx.fs_dir, "tmp/HOOK")
    shutil.copyfile(ctx.hook, target)
    st = os.stat(target)
    os.chmod(target, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with mount_session.MountSession(ctx) as session:
        session.allow_local_x_access()
        session.mount_sys()
        session.mount_dbus()
        chroot.chroot_run(ctx, "/tmp/HOOK")
