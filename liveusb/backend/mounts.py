"""Mount and unmount helpers with explicit acquisition evidence."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from . import is_mounted, run_ok
from .. import messages


MOUNT_CREATED = "created"
MOUNT_ALREADY_PRESENT = "already-mounted"
MOUNT_FAILED = "failed"
UNMOUNTED = "unmounted"
UNMOUNT_NOT_PRESENT = "not-mounted"
UNMOUNT_FAILED = "failed"
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True)
class MountAcquisition:
    source: str
    destination: str
    label: str
    outcome: str
    owned: bool = False
    created_directory: bool = False
    error: Optional[BaseException] = None


@dataclass(frozen=True)
class UnmountResult:
    destination: str
    label: str
    outcome: str
    error: Optional[BaseException] = None


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
            messages.error(
                "FileSystem path is not usable or has been corrupted!"
            )


def check_for_x():
    messages.info("Checking whether x-server is running")
    if not run_ok(
        ["pgrep", "Xorg"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ):
        messages.error("X-server is not running!")


def _mount_one(src, dst, label, bind_opts):
    if is_mounted(dst):
        messages.extra_warning(f"{label} is already mounted on", dst)
        return MountAcquisition(
            source=src,
            destination=dst,
            label=label,
            outcome=MOUNT_ALREADY_PRESENT,
        )

    messages.extra_info("Mounting", label)
    created_directory = not os.path.lexists(dst)
    try:
        os.makedirs(dst, exist_ok=True)
    except OSError as error:
        return MountAcquisition(
            source=src,
            destination=dst,
            label=label,
            outcome=MOUNT_FAILED,
            error=error,
        )

    command_error = None
    try:
        command_succeeded = run_ok(
            ["mount"] + bind_opts + [src, dst]
        )
    except Exception as error:
        command_succeeded = False
        command_error = error

    mounted_after = is_mounted(dst)
    if command_succeeded and mounted_after:
        return MountAcquisition(
            source=src,
            destination=dst,
            label=label,
            outcome=MOUNT_CREATED,
            owned=True,
            created_directory=created_directory,
        )

    error = command_error or messages.LiveUSBError(
        f"Unable to prove mount acquisition for {label}: {dst}"
    )
    messages.extra_error_no_exit(
        f"Unable to mount {label} to",
        dst,
    )
    return MountAcquisition(
        source=src,
        destination=dst,
        label=label,
        outcome=MOUNT_FAILED,
        owned=mounted_after,
        created_directory=created_directory,
        error=error,
    )


def mount_sys(ctx):
    return (
        _mount_one(
            "/dev",
            os.path.join(ctx.fs_dir, "dev"),
            "/dev",
            ["--rbind"],
        ),
        _mount_one(
            "/proc",
            os.path.join(ctx.fs_dir, "proc"),
            "/proc",
            ["--bind"],
        ),
        _mount_one(
            "/sys",
            os.path.join(ctx.fs_dir, "sys"),
            "/sys",
            ["--bind"],
        ),
    )


def _umount_one(dst, label, extra_error=False):
    if not is_mounted(dst):
        messages.extra_warning(f"{label} is not mounted on", dst)
        return UnmountResult(
            destination=dst,
            label=label,
            outcome=UNMOUNT_NOT_PRESENT,
        )

    messages.extra_info("Unmounting", label)
    command_error = None
    try:
        command_succeeded = run_ok(["umount", "-fl", dst])
    except Exception as error:
        command_succeeded = False
        command_error = error

    mounted_after = is_mounted(dst)
    if command_succeeded and not mounted_after:
        return UnmountResult(
            destination=dst,
            label=label,
            outcome=UNMOUNTED,
        )

    error = command_error or messages.LiveUSBError(
        f"Unable to prove unmount completion for {label}: {dst}"
    )
    if extra_error:
        messages.extra_error_no_exit(
            f"Unable to unmount {label} from",
            dst,
        )
    else:
        messages.extra_warning(
            f"Unable to unmount {label} from",
            dst,
        )
    return UnmountResult(
        destination=dst,
        label=label,
        outcome=UNMOUNT_FAILED,
        error=error,
    )


def umount_sys(ctx):
    return (
        _umount_one(
            os.path.join(ctx.fs_dir, "sys"),
            "/sys",
            extra_error=True,
        ),
        _umount_one(
            os.path.join(ctx.fs_dir, "proc"),
            "/proc",
            extra_error=True,
        ),
        _umount_one(
            os.path.join(ctx.fs_dir, "dev"),
            "/dev",
            extra_error=True,
        ),
    )


def mounted_paths(ctx):
    fs_dir = os.path.abspath(ctx.fs_dir)
    paths = []
    with open("/proc/mounts", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 2:
                continue
            mount_point = _MOUNT_ESCAPE.sub(
                lambda match: chr(int(match.group(1), 8)),
                fields[1],
            )
            absolute = os.path.abspath(mount_point)
            try:
                common = os.path.commonpath((fs_dir, absolute))
            except ValueError:
                continue
            if common == fs_dir:
                paths.append(absolute)
    return tuple(sorted(set(paths)))


def _is_preserved_mount(path, preserved_paths):
    for preserved in preserved_paths:
        try:
            common = os.path.commonpath((preserved, path))
        except ValueError:
            continue
        if common == preserved:
            return True
    return False


def recursive_umount(ctx, preserve=()):
    messages.info("Verbose unmounting")
    preserved_paths = tuple(
        os.path.abspath(path) for path in preserve
    )
    try:
        candidates = mounted_paths(ctx)
    except OSError as error:
        return (
            UnmountResult(
                destination=os.path.abspath(ctx.fs_dir),
                label="/",
                outcome=UNMOUNT_FAILED,
                error=error,
            ),
        )

    candidates = tuple(
        path
        for path in candidates
        if not _is_preserved_mount(path, preserved_paths)
    )
    candidates = tuple(
        sorted(
            candidates,
            key=lambda path: (path.count(os.sep), len(path), path),
            reverse=True,
        )
    )
    results = []
    for mount_point in candidates:
        label = os.path.relpath(mount_point, ctx.fs_dir)
        if label == ".":
            label = "/"
        else:
            label = "/" + label
        results.append(
            _umount_one(mount_point, label)
        )
    return tuple(results)


def mount_dbus(ctx):
    var_lib_dbus = os.path.join(ctx.fs_dir, "var", "lib", "dbus")
    first = _mount_one(
        "/var/lib/dbus",
        var_lib_dbus,
        "/var/lib/dbus",
        ["--bind"],
    )

    var_run = os.path.join(ctx.fs_dir, "var", "run")
    if os.path.islink(var_run):
        source = "/run/dbus"
        destination = os.path.join(ctx.fs_dir, "run", "dbus")
        label = "/run/dbus"
    else:
        source = "/var/run/dbus"
        destination = os.path.join(
            ctx.fs_dir,
            "var",
            "run",
            "dbus",
        )
        label = "/var/run/dbus"
    second = _mount_one(
        source,
        destination,
        label,
        ["--bind"],
    )
    return first, second


def allow_local_x_access():
    messages.info("Allowing local access to x-server")
    return run_ok(["xhost", "+local:"])


def block_local_x_access():
    messages.info("Blocking local access to x-server")
    return run_ok(["xhost", "-local:"])


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
