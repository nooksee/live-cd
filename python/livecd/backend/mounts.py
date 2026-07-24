"""Mount/unmount helpers, ported from .hidden/common."""

import os
import shutil
import subprocess

from . import is_mounted, run, run_ok
from .. import messages


def check_lock(ctx):
    messages.info("Checking for FileSystem lock")
    if ctx.force_chroot:
        messages.warning("FileSystem lock check skipped!")
        return
    lock = os.path.join(ctx.fs_dir, "tmp", "lock_chroot")
    if os.path.exists(lock):
        messages.error("FileSystem is locked!")


def check_fs_dir(ctx):
    messages.info("Checking FileSystem")
    if not os.path.isdir(ctx.fs_dir):
        messages.error("FileSystem path does not exist!")

    for sub in ("etc", "usr", "root"):
        if not os.path.isdir(os.path.join(ctx.fs_dir, sub)):
            messages.error("FileSystem path is not usable or has been corrupted!")


def check_for_x():
    messages.info("Checking whether x-server is running")
    if not run_ok(["pgrep", "Xorg"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
        messages.error("X-server is not running!")


def _mount_one(src, dst, label, bind_opts):
    if is_mounted(dst):
        messages.extra_warning(f"{label} is already mounted on", dst)
        return
    messages.extra_info("Mounting", label)
    os.makedirs(dst, exist_ok=True)
    if not run_ok(["mount"] + bind_opts + [src, dst]):
        messages.extra_error_no_exit(f"Unable to mount {label} to", dst)


def mount_sys(ctx):
    _mount_one("/dev", os.path.join(ctx.fs_dir, "dev"), "/dev", ["--rbind"])
    _mount_one("/proc", os.path.join(ctx.fs_dir, "proc"), "/proc", ["--bind"])
    _mount_one("/sys", os.path.join(ctx.fs_dir, "sys"), "/sys", ["--bind"])


def _umount_one(dst, label, extra_error=False):
    if not is_mounted(dst):
        messages.extra_warning(f"{label} is not mounted on", dst)
        return
    messages.extra_info("Unmounting", label)
    if not run_ok(["umount", "-fl", dst]):
        if extra_error:
            messages.extra_error(f"Unable to unmount {label} from", dst)
        else:
            messages.extra_warning(f"Unable to unmount {label} from", dst)


def umount_sys(ctx):
    _umount_one(os.path.join(ctx.fs_dir, "sys"), "/sys", extra_error=True)
    _umount_one(os.path.join(ctx.fs_dir, "proc"), "/proc", extra_error=True)
    _umount_one(os.path.join(ctx.fs_dir, "dev"), "/dev", extra_error=True)


def recursive_umount(ctx):
    messages.info("Verbose unmounting")
    try:
        with open("/proc/mounts") as fh:
            mounts = [line.split(" ")[1] for line in fh if ctx.fs_dir in line]
    except OSError:
        mounts = []
    mounts = [m.replace("\\040", " ") for m in mounts]
    for mount_point in mounts:
        label = mount_point.replace(ctx.fs_dir, "")
        messages.extra_info("Unmounting", label)
        if not run_ok(["umount", "-fl", mount_point]):
            messages.extra_warning("Unable to unmount", mount_point)


def mount_dbus(ctx):
    var_lib_dbus = os.path.join(ctx.fs_dir, "var", "lib", "dbus")
    os.makedirs(var_lib_dbus, exist_ok=True)
    messages.extra_info("Mounting", "/var/lib/dbus")
    if not run_ok(["mount", "--bind", "/var/lib/dbus", var_lib_dbus]):
        messages.extra_warning("Unable to mount /var/lib/dbus to", var_lib_dbus)

    var_run = os.path.join(ctx.fs_dir, "var", "run")
    if os.path.islink(var_run):
        run_dbus = os.path.join(ctx.fs_dir, "run", "dbus")
        messages.extra_info("Mounting", "/run/dbus")
        os.makedirs(run_dbus, exist_ok=True)
        if not run_ok(["mount", "--bind", "/run/dbus", run_dbus]):
            messages.extra_warning("Unable to mount /run/dbus to", run_dbus)
    else:
        var_run_dbus = os.path.join(ctx.fs_dir, "var", "run", "dbus")
        messages.extra_info("Mounting", "/var/run/dbus")
        os.makedirs(var_run_dbus, exist_ok=True)
        if not run_ok(["mount", "--bind", "/var/run/dbus", var_run_dbus]):
            messages.extra_warning("Unable to mount /var/run/dbus to", var_run_dbus)


def allow_local_x_access():
    messages.info("Allowing local access to x-server")
    run_ok(["xhost", "+local:"])


def block_local_x_access():
    messages.info("Blocking local access to x-server")
    run_ok(["xhost", "-local:"])


def purge_work_dirs(ctx):
    check_lock(ctx)
    recursive_umount(ctx)

    messages.extra_info("Purging", ctx.fs_dir)
    try:
        shutil.rmtree(ctx.fs_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError:
        messages.error("Unable to purge FileSystem directory")

    messages.extra_info("Purging", ctx.iso_dir)
    try:
        shutil.rmtree(ctx.iso_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError:
        messages.error("Unable to purge the ISO directory")
