"""Port of .hidden/scripts/rebuild: assemble the final bootable ISO image."""

import glob
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import date

from . import mounts, chroot, mount_session, run
from .. import constants, messages

_EFI_KERNEL_REFERENCE = b"vmlinuz.efi"
_MEDIA_SCAN_CHUNK_SIZE = 64 * 1024
_SHA256_CHUNK_SIZE = 1024 * 1024


def compression_probe_command(
    compression,
    source,
    output,
    executable="mksquashfs",
):
    """Return the bounded synthetic compressor-probe argv."""

    return (
        executable,
        source,
        output,
        "-comp",
        compression,
        "-processors",
        "1",
        "-no-progress",
    )


def mksquashfs_command(
    ctx,
    output_path,
    compression_supported,
    executable="mksquashfs",
    exclude_file=None,
):
    """Return the one accepted product-tree SquashFS argv."""

    selected_exclude_file = (
        constants.EXCLUDE_FILE
        if exclude_file is None
        else exclude_file
    )
    command = [
        executable,
        ctx.fs_dir,
        output_path,
        "-wildcards",
        "-ef",
        selected_exclude_file,
    ]
    if compression_supported:
        command.extend(("-comp", ctx.compression))
    return tuple(command)


def manifest_query_command(ctx, executable="chroot"):
    """Return the accepted package-manifest query argv."""

    return (
        executable,
        ctx.fs_dir,
        "dpkg-query",
        "-W",
        "--showformat=${Package} ${Version}\n",
    )


def genisoimage_command(
    volume_label,
    output_path,
    executable="genisoimage",
):
    """Return the accepted legacy ISO-generation argv."""

    return (
        executable,
        "-r",
        "-V",
        volume_label,
        "-b",
        "isolinux/isolinux.bin",
        "-c",
        "isolinux/boot.cat",
        "-cache-inodes",
        "-J",
        "-l",
        "-no-emul-boot",
        "-boot-load-size",
        "4",
        "-boot-info-table",
        "-o",
        output_path,
        "-input-charset",
        "utf-8",
        ".",
    )


def isohybrid_command(output_path, executable="isohybrid"):
    """Return the accepted legacy hybrid-mutation argv."""

    return executable, output_path


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


def _compression_is_supported(
    compression,
    probe=None,
    scratch_root=None,
):
    """Determine compressor support with one bounded synthetic image."""
    if not isinstance(compression, str) or not compression:
        return False
    selected_probe = subprocess.run if probe is None else probe
    try:
        with tempfile.TemporaryDirectory(
            prefix=".liveusb-compression-probe-",
            dir=scratch_root,
        ) as probe_root:
            source = os.path.join(probe_root, "empty-source")
            output = os.path.join(probe_root, "probe.squashfs")
            os.mkdir(source, 0o700)
            result = selected_probe(
                list(
                    compression_probe_command(
                        compression,
                        source,
                        output,
                    )
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if getattr(result, "returncode", 1) != 0:
                return False
            try:
                output_state = os.lstat(output)
            except FileNotFoundError:
                return False
            return (
                stat.S_ISREG(output_state.st_mode)
                and not stat.S_ISLNK(output_state.st_mode)
                and output_state.st_nlink == 1
            )
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to inspect mksquashfs compression capability"
        ) from error


def _plan_mksquashfs_command(ctx, output_path, probe=None):
    supported = _compression_is_supported(
        ctx.compression,
        probe=probe,
        scratch_root=ctx.work_dir,
    )
    if not supported:
        messages.warning(
            "Configured SquashFS compression is unsupported; "
            "the tool default will be used"
        )
    return list(
        mksquashfs_command(
            ctx,
            output_path,
            supported,
        )
    )


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


def _literal_media_node(root, relative_path, node_type):
    root = os.path.abspath(root)
    if (
        os.path.normpath(root) != root
        or os.path.realpath(root) != root
    ):
        return False
    cursor = root
    try:
        root_state = os.lstat(root)
        if (
            not stat.S_ISDIR(root_state.st_mode)
            or stat.S_ISLNK(root_state.st_mode)
        ):
            return False
        parts = relative_path.split(os.sep)
        for index, part in enumerate(parts):
            if not part or part in {".", ".."}:
                return False
            cursor = os.path.join(cursor, part)
            state = os.lstat(cursor)
            final = index == len(parts) - 1
            if stat.S_ISLNK(state.st_mode):
                return False
            if not final and not stat.S_ISDIR(state.st_mode):
                return False
        if os.path.realpath(cursor) != cursor:
            return False
        if node_type == "directory":
            return stat.S_ISDIR(state.st_mode)
        if node_type == "file":
            return stat.S_ISREG(state.st_mode)
    except OSError:
        return False
    return False


def _legacy_media_profile(ctx):
    requirements = (
        ("isolinux", "directory"),
        (os.path.join("isolinux", "isolinux.bin"), "file"),
        ("casper", "directory"),
        (".disk", "directory"),
    )
    return all(
        _literal_media_node(ctx.iso_dir, path, node_type)
        for path, node_type in requirements
    )


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


def _sidecar_payload(digest, iso_path):
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in digest
        )
    ):
        raise messages.LiveUSBError(
            "Final ISO SHA-256 digest is invalid"
        )
    return (
        f"{digest}  {os.path.basename(iso_path)}\n"
    ).encode("ascii")


def _require_regular_artifact(path):
    try:
        state = os.lstat(path)
    except OSError as error:
        raise messages.LiveUSBError(
            f"Final publication artifact is unavailable: {path}"
        ) from error
    if (
        not stat.S_ISREG(state.st_mode)
        or stat.S_ISLNK(state.st_mode)
        or state.st_nlink != 1
    ):
        raise messages.LiveUSBError(
            f"Final publication artifact is unsafe: {path}"
        )
    return state


def _validate_sha256_pair(iso_path, sidecar_path):
    iso_state = _require_regular_artifact(iso_path)
    _require_regular_artifact(sidecar_path)
    if stat.S_IMODE(iso_state.st_mode) != 0o555:
        raise messages.LiveUSBError(
            "Final ISO is not sealed read-only"
        )
    digest = _sha256_file(iso_path)
    expected = _sidecar_payload(digest, iso_path)
    try:
        with open(sidecar_path, "rb") as handle:
            actual = handle.read()
    except OSError as error:
        raise messages.LiveUSBError(
            "Unable to read final ISO SHA-256 evidence"
        ) from error
    if actual != expected:
        raise messages.LiveUSBError(
            "Final ISO SHA-256 evidence does not match"
        )
    return digest


def _validate_prior_pair(iso_path, sidecar_path):
    iso_exists = os.path.lexists(iso_path)
    sidecar_exists = os.path.lexists(sidecar_path)
    if not iso_exists and not sidecar_exists:
        return None
    if iso_exists != sidecar_exists:
        raise messages.LiveUSBError(
            "Prior final ISO publication pair is incomplete"
        )
    return _validate_sha256_pair(iso_path, sidecar_path)


def _apply_legacy_hybrid_mutation(
    ctx,
    session,
    runner=None,
    locator=None,
):
    if not _legacy_media_profile(ctx):
        raise messages.LiveUSBError(
            "ISO finalization requires the accepted legacy-media profile"
        )
    selected_runner = run if runner is None else runner
    selected_locator = shutil.which if locator is None else locator
    isohybrid_path = selected_locator("isohybrid")
    if isohybrid_path is None:
        raise messages.LiveUSBError(
            "Required legacy finalization tool is unavailable: isohybrid"
        )
    iso_path = session.begin_external_primary_mutation()
    try:
        result = selected_runner(
            list(isohybrid_command(iso_path, isohybrid_path))
        )
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to apply legacy hybrid ISO mutation"
        ) from error
    if getattr(result, "returncode", 1) != 0:
        raise messages.LiveUSBError(
            "Unable to apply legacy hybrid ISO mutation"
        )
    session.finish_external_primary_mutation()
    return session.seal_external_primary()


def _finish_external_publication(
    session,
    iso_path,
    sidecar_path,
):
    view = session.external_publication_view(
        iso_path,
        sidecar_path,
        "final-image",
    )
    if view["phase"] == "complete":
        digest = _validate_sha256_pair(
            view["primary_final"],
            view["evidence_final"],
        )
        if view["digest"] != digest:
            raise messages.LiveUSBError(
                "Recovered completed publication digest changed"
            )
        return session.acknowledge_external_publication()
    if view["phase"] == "sealed":
        candidate_path = view["primary_candidate"]
        digest = _sha256_file(candidate_path)
        if view["digest"] is None:
            session.record_external_digest(digest)
        elif view["digest"] != digest:
            raise messages.LiveUSBError(
                "Recovered final ISO candidate digest changed"
            )
        view = session.external_publication_view(
            iso_path,
            sidecar_path,
            "final-image",
        )
        if view["evidence_stage"] == "planned":
            session.write_external_evidence(
                _sidecar_payload(digest, iso_path)
            )
    return session.publish_external_pair(
        validator=_validate_sha256_pair,
    )


def _build_locked_final_image_steps(
    ctx,
    session,
    dist,
    arch,
    version,
    codename,
    disk_dir,
    casper_dir,
    iso_path,
    sha256_path,
):
    _validate_prior_pair(iso_path, sha256_path)
    publication = session.begin_external_publication(
        iso_path,
        sha256_path,
        "final-image",
    )

    messages.info("Creating squashed FileSystem")
    squashfs_path = os.path.join(
        casper_dir,
        "filesystem.squashfs",
    )
    _build_squashfs(ctx, squashfs_path)

    messages.info("Checking FileSystem size")
    fs_size = os.path.getsize(squashfs_path)
    if fs_size > 4_000_000_000:
        messages.error(
            "The squashed FileSystem size is greater than "
            "4 gigabytes!"
        )

    messages.info("Creating filesystem.size")
    with open(
        os.path.join(casper_dir, "filesystem.size"),
        "w",
    ) as fh:
        fh.write(f"{fs_size}\n")

    messages.info("Creating filesystem.manifest")
    manifest_result = subprocess.run(
        list(manifest_query_command(ctx)),
        stdout=subprocess.PIPE,
        text=True,
    )
    if manifest_result.returncode != 0:
        messages.error(
            "Unable to create the filesystem.manifest!"
        )
    manifest_path = os.path.join(
        casper_dir,
        "filesystem.manifest",
    )
    with open(manifest_path, "w") as fh:
        fh.write(manifest_result.stdout)

    messages.info("Creating filesystem.manifest-desktop")
    manifest_desktop_path = os.path.join(
        casper_dir,
        "filesystem.manifest-desktop",
    )
    shutil.copyfile(manifest_path, manifest_desktop_path)
    exclude_pkgs = (
        "ubiquity",
        "casper",
        "live-initramfs",
        "user-setup",
        "discover1",
        "xresprobe",
        "libdebian-installer4",
    )
    with open(manifest_desktop_path) as fh:
        lines = [
            line
            for line in fh
            if not any(pkg in line for pkg in exclude_pkgs)
        ]
    with open(manifest_desktop_path, "w") as fh:
        fh.writelines(lines)

    messages.info("Creating README.diskdefines")
    with open(
        os.path.join(ctx.iso_dir, "README.diskdefines"),
        "w",
    ) as fh:
        fh.write(
            f"#define DISKNAME  {dist} {version} "
            f"\"{codename}\" - Release {arch}\n"
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
        fh.write(
            f'{dist} {version} "{codename}" - '
            f"Release {arch} ({today})\n"
        )

    messages.info("Creating MD5Sum")
    md5sum_path = os.path.join(ctx.iso_dir, "md5sum.txt")
    _write_md5sums(ctx.iso_dir, md5sum_path)

    messages.info("Creating image file")
    candidate_path = session.begin_external_primary_write()
    try:
        genisoimage_result = run(
            list(
                genisoimage_command(
                    f"{dist}-{arch}-{version}",
                    candidate_path,
                )
            ),
            cwd=ctx.iso_dir,
        )
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to execute genisoimage"
        ) from error
    if getattr(genisoimage_result, "returncode", 1) != 0:
        raise messages.LiveUSBError("Unable to create image file")
    session.finish_external_primary_write()

    _apply_legacy_hybrid_mutation(ctx, session)
    digest = _sha256_file(publication["primary_candidate"])
    session.record_external_digest(digest)
    session.write_external_evidence(
        _sidecar_payload(digest, iso_path)
    )
    return session.publish_external_pair(
        validator=_validate_sha256_pair,
    )


def _build_locked_final_image(
    ctx,
    session,
    dist,
    arch,
    version,
    codename,
    disk_dir,
    casper_dir,
    iso_path,
    sha256_path,
):
    try:
        return _build_locked_final_image_steps(
            ctx,
            session,
            dist,
            arch,
            version,
            codename,
            disk_dir,
            casper_dir,
            iso_path,
            sha256_path,
        )
    except messages.LiveUSBError:
        raise
    except Exception as error:
        raise messages.LiveUSBError(
            "Unable to complete the final image transaction"
        ) from error


def _report_rebuild_success():
    messages.info(
        "Distribution rebuild completed. Use 'exit' to quit properly."
    )
    if shutil.which("zenity"):
        subprocess.run(
            [
                "zenity",
                "--info",
                "--window-icon=info",
                "--text",
                "Distribution rebuild completed. "
                "Use 'exit' to quit properly.",
            ]
        )


def run_rebuild(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    with mount_session.MountSession(ctx) as recovery_session:
        if recovery_session.has_external_publication:
            recovered = (
                recovery_session.current_external_publication()
            )
            if recovered["purpose"] != "final-image":
                raise messages.LiveUSBError(
                    "Recovered external publication purpose "
                    "is not a final image"
                )
            _finish_external_publication(
                recovery_session,
                recovered["primary_final"],
                recovered["evidence_final"],
            )
            _report_rebuild_success()
            return

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

    with mount_session.MountSession(ctx) as final_session:
        _build_locked_final_image(
            ctx,
            final_session,
            dist,
            arch,
            version,
            codename,
            disk_dir,
            casper_dir,
            iso_path,
            sha256_path,
        )
    _report_rebuild_success()


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
