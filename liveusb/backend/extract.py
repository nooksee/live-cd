"""Port of .hidden/scripts/extract: extract a LiveUSB ISO image into the work dir."""

import os
import subprocess
import tempfile

from . import chroot, mount_session, mounts, run
from .. import messages


def _clean(ctx):
    """Purge only after MountSession has recovered stale resources."""
    mounts.purge_work_dirs(ctx)


def run_extract(ctx):
    with mount_session.MountSession(ctx):
        return _run_extract_locked(ctx)


def _run_extract_locked(ctx):
    if not os.path.exists(ctx.iso):
        messages.error("The image file does not exist!")

    if os.path.isdir(ctx.fs_dir) or os.path.isdir(ctx.iso_dir):
        _clean(ctx)
        chroot.create_work_dirs(ctx)
    else:
        chroot.create_work_dirs(ctx)

    os.makedirs(ctx.mount_dir, exist_ok=True)
    mount_point = tempfile.mkdtemp(dir=ctx.mount_dir)

    owned_mount = None
    primary_error = None
    try:
        messages.info("Mounting image file")
        owned_mount = _acquire_iso_mount(ctx, mount_point)

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
            _clean(ctx)
            raise messages.LiveUSBError(
                "not a usable image file"
            )

        messages.info("Extracting FileSystem")
        if run(
            [
                "unsquashfs",
                "-f",
                "-d",
                ctx.fs_dir,
                os.path.join(
                    mount_point,
                    "casper/filesystem.squashfs",
                ),
            ]
        ).returncode != 0:
            messages.error(
                "Unable to extract filesystem.squashfs!"
            )

        messages.info("Checking architecture")
        arch_result = subprocess.run(
            [
                "chroot",
                ctx.fs_dir,
                "dpkg",
                "--print-architecture",
            ],
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
            _clean(ctx)
            raise messages.LiveUSBError(
                "architecture mismatch"
            )

        messages.info("Copying extracted files")
        rsync_result = run(
            [
                "rsync",
                "--exclude=/casper/*",
                "--exclude=/md5sum.txt",
                "--exclude=/README.diskdefines",
                "-a",
                mount_point + "/",
                ctx.iso_dir,
            ]
        )
        if rsync_result.returncode != 0:
            messages.error("Unable to rsync files!")
    except BaseException as error:
        primary_error = error

    cleanup_error = None
    if owned_mount is not None:
        messages.info("Unmounting image file")
        try:
            _release_iso_mount(owned_mount)
        except BaseException as error:
            cleanup_error = error
    try:
        _remove_empty_mount_point(mount_point)
    except BaseException as error:
        if cleanup_error is None:
            cleanup_error = error

    if cleanup_error is not None:
        if primary_error is not None:
            raise cleanup_error from primary_error
        raise cleanup_error
    if primary_error is not None:
        raise primary_error


def _acquire_iso_mount(
    ctx,
    mount_point,
    mountinfo_reader=mounts.read_mountinfo,
    runner=run,
):
    request = mounts.MountRequest(
        source=os.path.abspath(ctx.iso),
        destination=os.path.abspath(mount_point),
        label="ISO image",
        options=("-t", "iso9660", "-o", "ro,loop"),
        recursive=False,
    )
    before = tuple(mountinfo_reader())
    before_scoped = mounts.mounts_under(
        before,
        request.destination,
        include_root=True,
    )
    if before_scoped:
        raise mounts.MountEvidenceError(
            "ISO mount destination already contains mount evidence"
        )
    result = runner(
        [
            "mount",
            "-t",
            "iso9660",
            "-o",
            "ro,loop",
            request.source,
            request.destination,
        ]
    )
    after = tuple(mountinfo_reader())
    after_scoped = mounts.mounts_under(
        after,
        request.destination,
        include_root=True,
    )
    if getattr(result, "returncode", 1) != 0:
        if mounts.identity_map(after_scoped) != mounts.identity_map(
            before_scoped
        ):
            raise mounts.MountEvidenceError(
                "Failed ISO mount changed unowned evidence"
            )
        raise messages.LiveUSBError(
            "Unable to mount image file"
        )
    owned = mounts.attributable_mounts(
        request,
        before_scoped,
        after_scoped,
    )
    if len(owned) != 1:
        raise mounts.MountEvidenceError(
            "ISO mount ownership is ambiguous"
        )
    return owned[0]


def _release_iso_mount(
    identity,
    mountinfo_reader=mounts.read_mountinfo,
    runner=run,
):
    before = tuple(mountinfo_reader())
    at_path = mounts.mounts_at(
        before,
        identity.mount_point,
    )
    if len(at_path) != 1 or at_path[0].key != identity.key:
        raise mounts.MountEvidenceError(
            "ISO mount identity changed before cleanup"
        )
    result = runner(["umount", "-f", identity.mount_point])
    after = tuple(mountinfo_reader())
    before_keys = set(mounts.identity_map(before))
    after_keys = set(mounts.identity_map(after))
    if (
        getattr(result, "returncode", 1) != 0
        or identity.key in after_keys
        or mounts.mounts_at(after, identity.mount_point)
        or before_keys - after_keys != {identity.key}
    ):
        raise mounts.MountEvidenceError(
            "ISO unmount completion is not exact"
        )


def _remove_empty_mount_point(
    mount_point,
    mountinfo_reader=mounts.read_mountinfo,
):
    if mounts.mounts_under(
        tuple(mountinfo_reader()),
        mount_point,
        include_root=True,
    ):
        raise mounts.MountEvidenceError(
            "ISO mountpoint still contains mount evidence"
        )
    try:
        os.rmdir(mount_point)
    except FileNotFoundError:
        return
