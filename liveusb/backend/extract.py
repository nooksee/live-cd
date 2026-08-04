"""Port of .hidden/scripts/extract: extract a LiveUSB ISO image into the work dir."""

import os
import subprocess

from . import chroot, mount_session, mounts, run
from .. import messages


def unsquashfs_command(ctx, mount_point, executable="unsquashfs"):
    """Return the accepted legacy filesystem-extraction argv."""

    return (
        executable,
        "-f",
        "-d",
        ctx.fs_dir,
        os.path.join(
            mount_point,
            "casper",
            "filesystem.squashfs",
        ),
    )


def target_architecture_command(ctx, executable="chroot"):
    """Return the accepted target-architecture observation argv."""

    return (
        executable,
        ctx.fs_dir,
        "dpkg",
        "--print-architecture",
    )


def media_tree_copy_command(ctx, mount_point, executable="rsync"):
    """Return the accepted legacy ISO-tree copy argv."""

    return (
        executable,
        "--exclude=/casper/*",
        "--exclude=/md5sum.txt",
        "--exclude=/README.diskdefines",
        "-a",
        mount_point + "/",
        ctx.iso_dir,
    )


def _clean(ctx):
    """Purge only after MountSession has recovered stale resources."""
    mounts.purge_work_dirs(ctx)


def run_extract(ctx):
    mounts.validate_extract_layout(ctx)
    with mount_session.MountSession(ctx) as session:
        return _run_extract_locked(ctx, session)


def _run_extract_locked(ctx, session):
    if not os.path.exists(ctx.iso):
        messages.error("The image file does not exist!")

    if os.path.isdir(ctx.fs_dir) or os.path.isdir(ctx.iso_dir):
        _clean(ctx)
        chroot.create_work_dirs(ctx)
    else:
        chroot.create_work_dirs(ctx)

    os.makedirs(ctx.mount_dir, exist_ok=True)

    mount_point = None
    primary_error = None
    purge_after_cleanup = False
    try:
        messages.info("Mounting image file")
        acquisition = session.mount_iso()
        mount_point = acquisition.destination

        messages.info("Checking image file")
        required = ["casper", ".disk", "isolinux"]
        ok = all(
            os.path.isdir(os.path.join(mount_point, directory))
            for directory in required
        )
        ok = ok and os.path.exists(
            os.path.join(
                mount_point,
                "casper/filesystem.squashfs",
            )
        )
        if not ok:
            messages.error_no_exit(
                "This is not a usable image file!"
            )
            purge_after_cleanup = True
            raise messages.LiveUSBError(
                "not a usable image file"
            )

        messages.info("Extracting FileSystem")
        if run(list(unsquashfs_command(ctx, mount_point))).returncode != 0:
            messages.error(
                "Unable to extract filesystem.squashfs!"
            )

        messages.info("Checking architecture")
        arch_result = subprocess.run(
            list(target_architecture_command(ctx)),
            stdout=subprocess.PIPE,
            text=True,
        )
        if arch_result.returncode != 0:
            messages.error(
                "Unable to chroot and get architecture "
                "of root FileSystem!"
            )
        arch = arch_result.stdout.strip()
        machine = subprocess.run(
            ["uname", "-m"],
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        if arch == "amd64" and machine != "x86_64":
            messages.error_no_exit(
                "The image file's architecture is amd64, yours is not!"
            )
            purge_after_cleanup = True
            raise messages.LiveUSBError(
                "architecture mismatch"
            )

        messages.info("Copying extracted files")
        rsync_result = run(
            list(media_tree_copy_command(ctx, mount_point))
        )
        if rsync_result.returncode != 0:
            messages.error("Unable to rsync files!")
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    if mount_point is not None:
        messages.info("Unmounting image file")
    try:
        session.cleanup()
    except BaseException as error:
        cleanup_error = error

    if purge_after_cleanup and cleanup_error is None:
        try:
            _clean(ctx)
        except BaseException as error:
            cleanup_error = error

    if cleanup_error is not None:
        if primary_error is not None:
            raise cleanup_error from primary_error
        raise cleanup_error
    if primary_error is not None:
        raise primary_error
