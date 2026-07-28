"""Port of .hidden/scripts/rebuild: assemble the final bootable ISO image."""

import glob
import hashlib
import os
import shutil
import subprocess
from datetime import date

from . import mounts, chroot, mount_session, run
from .. import constants, messages

_EFI_KERNEL_REFERENCE = b"vmlinuz.efi"
_MEDIA_SCAN_CHUNK_SIZE = 64 * 1024
_SHA256_CHUNK_SIZE = 1024 * 1024


def _grep_value(path, key):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith(key):
                    return line[len(key):].strip().strip('"')
    except OSError:
        return None
    return None


def _latest_glob(pattern):
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _media_references_vmlinuz_efi(root):
    overlap_size = len(_EFI_KERNEL_REFERENCE) - 1
    for path in _walk_files(root):
        try:
            with open(path, "rb") as fh:
                overlap = b""
                while True:
                    chunk = fh.read(_MEDIA_SCAN_CHUNK_SIZE)
                    if not chunk:
                        break
                    searchable = overlap + chunk
                    if _EFI_KERNEL_REFERENCE in searchable:
                        return True
                    overlap = searchable[-overlap_size:]
        except OSError:
            continue
    return False


def _compression_is_supported(compression, probe=None):
    """Determine compressor support without creating an image."""
    if not isinstance(compression, str) or not compression:
        return False
    selected_probe = subprocess.run if probe is None else probe
    try:
        result = selected_probe(
            ["mksquashfs", "-help-comp", compression],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to inspect mksquashfs compression capability"
        ) from error
    return getattr(result, "returncode", 1) == 0


def _plan_mksquashfs_command(ctx, output_path, probe=None):
    command = [
        "mksquashfs",
        ctx.fs_dir,
        output_path,
        "-wildcards",
        "-ef",
        constants.EXCLUDE_FILE,
    ]
    if _compression_is_supported(ctx.compression, probe=probe):
        command.extend(("-comp", ctx.compression))
    else:
        messages.warning(
            "Configured SquashFS compression is unsupported; "
            "the tool default will be used"
        )
    return command


def _build_squashfs(ctx, output_path, probe=None, runner=None):
    command = _plan_mksquashfs_command(
        ctx,
        output_path,
        probe=probe,
    )
    selected_runner = run if runner is None else runner
    try:
        result = selected_runner(command)
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to execute mksquashfs"
        ) from error
    if getattr(result, "returncode", 1) != 0:
        raise messages.LiveUSBError(
            "Unable to squash the FileSystem"
        )
    return command


def _legacy_media_profile(ctx):
    return all((
        os.path.isdir(os.path.join(ctx.iso_dir, "isolinux")),
        os.path.isfile(
            os.path.join(ctx.iso_dir, "isolinux", "isolinux.bin")
        ),
        os.path.isdir(os.path.join(ctx.iso_dir, "casper")),
        os.path.isdir(os.path.join(ctx.iso_dir, ".disk")),
    ))


def _remove_stale_outputs(paths):
    for path in paths:
        if not os.path.lexists(path):
            continue
        messages.extra_info("Purging", path)
        try:
            os.remove(path)
        except OSError as error:
            raise messages.LiveUSBError(
                f"Unable to delete stale rebuild output: {path}"
            ) from error


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(
                lambda: handle.read(_SHA256_CHUNK_SIZE),
                b"",
            ):
                digest.update(chunk)
    except OSError as error:
        raise messages.LiveUSBError(
            f"Unable to hash final image: {path}"
        ) from error
    return digest.hexdigest()


def _sha256_temporary_path(sidecar_path):
    return sidecar_path + ".tmp"


def _close_quietly(descriptor):
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _remove_quietly(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _publish_sha256(iso_path, sidecar_path):
    """Publish one complete sidecar through an atomic replacement."""
    digest = _sha256_file(iso_path)
    temporary_path = _sha256_temporary_path(sidecar_path)
    descriptor = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary_path, flags, 0o644)
        payload = f"{digest}  {iso_path}\n".encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short SHA-256 sidecar write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, sidecar_path)
        directory_descriptor = os.open(
            os.path.dirname(sidecar_path),
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception as error:
        _close_quietly(descriptor)
        _remove_quietly(temporary_path)
        _remove_quietly(sidecar_path)
        raise messages.LiveUSBError(
            "Unable to publish final ISO SHA-256 evidence"
        ) from error
    return digest


def _finalize_legacy_iso(ctx, iso_path, sidecar_path, runner=None):
    if not _legacy_media_profile(ctx):
        raise messages.LiveUSBError(
            "ISO finalization requires the accepted legacy-media profile"
        )
    selected_runner = run if runner is None else runner
    try:
        result = selected_runner(["isohybrid", iso_path])
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to apply legacy hybrid ISO mutation"
        ) from error
    if getattr(result, "returncode", 1) != 0:
        raise messages.LiveUSBError(
            "Unable to apply legacy hybrid ISO mutation"
        )
    try:
        os.chmod(iso_path, 0o555)
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to seal the finalized ISO read-only"
        ) from error
    return _publish_sha256(iso_path, sidecar_path)


def run_rebuild(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.update_distro_name(ctx)
    chroot.check_sources_list()

    isolinux_dir = os.path.join(ctx.iso_dir, "isolinux")
    if not os.path.isdir(isolinux_dir):
        messages.error(f"{isolinux_dir} does not exist!")

    disk_dir = os.path.join(ctx.iso_dir, ".disk")
    if not os.path.isdir(disk_dir):
        messages.extra_warning("Creating", disk_dir)
        os.makedirs(disk_dir, exist_ok=True)

    casper_dir = os.path.join(ctx.iso_dir, "casper")
    if not os.path.isdir(casper_dir):
        messages.extra_warning("Creating", casper_dir)
        os.makedirs(casper_dir, exist_ok=True)

    cd_type = os.path.join(disk_dir, "cd_type")
    if not os.path.exists(cd_type):
        messages.extra_warning("Creating", cd_type)
        with open(cd_type, "w") as fh:
            fh.write("full_cd/single\n")

    # NB: faithfully ported from the original bash, which checks
    # $WORK_DIR/usr/bin/ubiquity rather than $WORK_DIR/FileSystem/usr/bin/ubiquity.
    base_installable = os.path.join(disk_dir, "base_installable")
    if os.path.exists(os.path.join(ctx.work_dir, "usr/bin/ubiquity")) and not os.path.exists(base_installable):
        messages.extra_warning("Creating", base_installable)
        open(base_installable, "w").close()
    else:
        try:
            os.remove(base_installable)
        except OSError:
            pass

    casper_uuid = os.path.join(disk_dir, "casper-uuid-generic")
    if not os.path.exists(casper_uuid):
        messages.extra_warning("Creating", casper_uuid)
        with open(casper_uuid, "w") as fh:
            fh.write("f01d0b93-4f0e-4e95-93ae-e3d0e114d4f7\n")

    release_notes_url_path = os.path.join(disk_dir, "release_notes_url")
    if not os.path.exists(release_notes_url_path):
        messages.extra_warning("Creating", release_notes_url_path)
        with open(release_notes_url_path, "w") as fh:
            fh.write("http://www.ubuntu.com/getubuntu/releasenotes\n")

    lsb_release_path = os.path.join(ctx.fs_dir, "etc/lsb-release")
    if not os.path.exists(lsb_release_path):
        messages.error(f"{lsb_release_path} does not exist!")

    casper_conf_path = os.path.join(ctx.fs_dir, "etc/casper.conf")
    if not os.path.exists(casper_conf_path):
        messages.error(f"{casper_conf_path} does not exist!")

    messages.info("Loading distribution information")
    arch_result = subprocess.run(["chroot", ctx.fs_dir, "dpkg", "--print-architecture"], stdout=subprocess.PIPE, text=True)
    if arch_result.returncode != 0:
        messages.error("Unable to chroot and get the architecture of the distribution FileSystem!")
    arch = arch_result.stdout.strip()

    with open(release_notes_url_path, errors="replace") as fh:
        release_notes_url = fh.read().strip()

    dist = _grep_value(lsb_release_path, "DISTRIB_ID=")
    version = _grep_value(lsb_release_path, "DISTRIB_RELEASE=")
    codename = _grep_value(lsb_release_path, "DISTRIB_CODENAME=") or ""
    _live_username = _grep_value(casper_conf_path, "export USERNAME=")
    if dist is None:
        messages.error("Unable to chroot and get the distribution identification")
    if version is None:
        messages.error("Unable to get the distribution version")

    iso_path = os.path.join(ctx.work_dir, f"{dist}-{arch}-{version}.iso")
    sha256_path = os.path.join(
        ctx.work_dir,
        f"{dist}-{arch}-{version}.sha256",
    )
    _remove_stale_outputs((
        iso_path,
        sha256_path,
        _sha256_temporary_path(sha256_path),
    ))
    to_clean = [
        os.path.join(casper_dir, "filesystem.squashfs"),
        os.path.join(casper_dir, "initrd.lz"),
        os.path.join(casper_dir, "vmlinuz"),
        os.path.join(casper_dir, "vmlinuz.efi"),
        os.path.join(casper_dir, "filesystem.manifest"),
        os.path.join(casper_dir, "filesystem.manifest-desktop"),
        os.path.join(casper_dir, "filesystem.size"),
        os.path.join(casper_dir, "README.diskdefines"),
        os.path.join(ctx.iso_dir, "md5sum.txt"),
    ]
    for path in to_clean:
        if os.path.exists(path):
            messages.extra_info("Purging", path)
            try:
                os.remove(path)
            except OSError:
                messages.extra_warning("Unable to delete", path)

    boot_dir = os.path.join(ctx.fs_dir, "boot")
    initrd_source = _latest_glob(os.path.join(boot_dir, "initrd.img-*"))
    vmlinuz_source = _latest_glob(os.path.join(boot_dir, "vmlinuz-*"))

    missing_kernel = (
        not initrd_source
        or not vmlinuz_source
        or not os.path.exists(initrd_source)
        or not os.path.exists(vmlinuz_source)
    )
    with mount_session.MountSession(ctx) as session:
        session.mount_sys()
        if missing_kernel:
            messages.info("Purging Kernels (if any)")
            chroot.chroot_run(
                ctx,
                "apt-get",
                "purge",
                "--yes",
                "linux-image*",
                "linux-headers*",
                "-qq",
            )
            messages.info("Installing Kernel")
            chroot.chroot_run(
                ctx,
                "apt-get",
                "install",
                "--yes",
                "linux-image-generic",
                "linux-headers-generic",
                "-qq",
            )
        else:
            messages.info("Updating kernel image")
            chroot.chroot_run(
                ctx,
                "update-initramfs",
                "-k",
                "all",
                "-t",
                "-u",
            )

    if missing_kernel:
        initrd_source = _latest_glob(
            os.path.join(boot_dir, "initrd.img-*")
        )
        vmlinuz_source = _latest_glob(
            os.path.join(boot_dir, "vmlinuz-*")
        )

    messages.extra_info("Copying initrd", os.path.relpath(initrd_source, boot_dir))
    shutil.copyfile(initrd_source, os.path.join(casper_dir, "initrd.lz"))

    uses_efi = _media_references_vmlinuz_efi(ctx.iso_dir)
    if uses_efi:
        messages.extra_info("Copying vmlinuz.efi", os.path.relpath(vmlinuz_source, boot_dir))
        shutil.copyfile(vmlinuz_source, os.path.join(casper_dir, "vmlinuz.efi"))
    else:
        messages.extra_info("Copying vmlinuz", os.path.relpath(vmlinuz_source, boot_dir))
        shutil.copyfile(vmlinuz_source, os.path.join(casper_dir, "vmlinuz"))

    if ctx.boot_files:
        messages.info("Deleting boot files")
        for pattern in ("initrd.img*", "vmlinuz*", "config*"):
            for path in glob.glob(os.path.join(boot_dir, pattern)):
                try:
                    os.remove(path)
                except OSError:
                    pass

    messages.info("Creating squashed FileSystem")
    squashfs_path = os.path.join(casper_dir, "filesystem.squashfs")
    _build_squashfs(ctx, squashfs_path)

    messages.info("Checking FileSystem size")
    fs_size = os.path.getsize(squashfs_path)
    if fs_size > 4_000_000_000:
        messages.error("The squashed FileSystem size is greater than 4 gigabytes!")

    messages.info("Creating filesystem.size")
    with open(os.path.join(casper_dir, "filesystem.size"), "w") as fh:
        fh.write(f"{fs_size}\n")

    messages.info("Creating filesystem.manifest")
    manifest_result = subprocess.run(
        ["chroot", ctx.fs_dir, "dpkg-query", "-W", "--showformat=${Package} ${Version}\n"],
        stdout=subprocess.PIPE,
        text=True,
    )
    if manifest_result.returncode != 0:
        messages.error("Unable to create the filesystem.manifest!")
    manifest_path = os.path.join(casper_dir, "filesystem.manifest")
    with open(manifest_path, "w") as fh:
        fh.write(manifest_result.stdout)

    messages.info("Creating filesystem.manifest-desktop")
    manifest_desktop_path = os.path.join(casper_dir, "filesystem.manifest-desktop")
    shutil.copyfile(manifest_path, manifest_desktop_path)
    exclude_pkgs = ("ubiquity", "casper", "live-initramfs", "user-setup", "discover1", "xresprobe", "libdebian-installer4")
    with open(manifest_desktop_path) as fh:
        lines = [line for line in fh if not any(pkg in line for pkg in exclude_pkgs)]
    with open(manifest_desktop_path, "w") as fh:
        fh.writelines(lines)

    messages.info("Creating README.diskdefines")
    with open(os.path.join(ctx.iso_dir, "README.diskdefines"), "w") as fh:
        fh.write(
            f"#define DISKNAME  {dist} {version} \"{codename}\" - Release {arch}\n"
            "#define TYPE  binary\n"
            "#define TYPEbinary  1\n"
            f"#define ARCH  {arch}\n"
            f"#define ARCH{arch}  1\n"
            "#define DISKNUM  1\n"
            "#define DISKNUM1  1\n"
            "#define TOTALNUM  0\n"
            "#define TOTALNUM0  1\n"
        )

    messages.info("Creating disk info")
    today = date.today().strftime("%Y%m%d")
    with open(os.path.join(disk_dir, "info"), "w") as fh:
        fh.write(f'{dist} {version} "{codename}" - Release {arch} ({today})\n')

    messages.info("Creating MD5Sum")
    md5sum_path = os.path.join(ctx.iso_dir, "md5sum.txt")
    _write_md5sums(ctx.iso_dir, md5sum_path)

    messages.info("Creating image file")
    try:
        genisoimage_result = run([
            "genisoimage",
            "-r",
            "-V", f"{dist}-{arch}-{version}",
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-cache-inodes",
            "-J",
            "-l",
            "-no-emul-boot",
            "-boot-load-size", "4",
            "-boot-info-table",
            "-o", iso_path,
            "-input-charset", "utf-8",
            ".",
        ], cwd=ctx.iso_dir)
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to execute genisoimage"
        ) from error
    if getattr(genisoimage_result, "returncode", 1) != 0:
        raise messages.LiveUSBError("Unable to create image file")

    _finalize_legacy_iso(ctx, iso_path, sha256_path)
    messages.info("Distribution rebuild completed. Use 'exit' to quit properly.")
    if shutil.which("zenity"):
        subprocess.run([
            "zenity", "--info", "--window-icon=info",
            "--text", "Distribution rebuild completed. Use 'exit' to quit properly.",
        ])


def _walk_files(root):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(dirpath, name)


def _write_md5sums(root, output_path):
    import hashlib

    with open(output_path, "w") as out:
        for path in sorted(_walk_files(root)):
            if os.path.basename(path) == "md5sum.txt":
                continue
            digest = hashlib.md5()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
            rel = os.path.relpath(path, root)
            out.write(f"{digest.hexdigest()}  ./{rel}\n")
