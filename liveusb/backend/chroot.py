"""chroot execution and related helpers, ported from .hidden/common."""

import glob
import os
import shutil

from . import run, run_ok
from .transaction import ChrootTransaction
from .. import messages


def create_work_dirs(ctx):
    messages.info("Creating work directories")
    try:
        os.makedirs(ctx.fs_dir, exist_ok=True)
        os.makedirs(ctx.iso_dir, exist_ok=True)
    except OSError:
        messages.error("Unable to create work directories!")


def check_sources_list():
    # FIXME: strip it from all scripts, this function was bogus (ported as-is).
    pass


def update_distro_name(ctx):
    software_center = os.path.join(ctx.fs_dir, "usr/bin/software-center")
    if not os.path.exists(software_center):
        return

    lsb_release = os.path.join(ctx.fs_dir, "etc/lsb-release")
    cust_rel = ""
    for line in messages_safe_read(lsb_release):
        if line.startswith("DISTRIB_ID="):
            cust_rel = line[len("DISTRIB_ID="):].strip().strip('"')
            break

    if not cust_rel or cust_rel == "Ubuntu":
        return

    messages.info("Updating distribution code name")
    cust_rel_class = "".join(w[:1].upper() + w[1:].lower() for w in cust_rel.split())
    cust_rel_module = cust_rel.lower()

    distro_dir = os.path.join(ctx.fs_dir, "usr/share/software-center/softwarecenter/distro")
    templates_dir = os.path.join(ctx.fs_dir, "usr/share/python-apt/templates")

    lower_ubuntu_py = os.path.join(distro_dir, "ubuntu.py")
    upper_ubuntu_py = os.path.join(distro_dir, "Ubuntu.py")

    if not os.path.exists(lower_ubuntu_py):
        src, target = upper_ubuntu_py, os.path.join(distro_dir, f"{cust_rel_class}.py")
    else:
        src, target = lower_ubuntu_py, os.path.join(distro_dir, f"{cust_rel_module}.py")

    try:
        shutil.copyfile(src, target)
        with open(target) as fh:
            content = fh.read()
        content = content.replace("class Ubuntu(Debian):", f"class {cust_rel_class}(Debian):")
        with open(target, "w") as fh:
            fh.write(content)
        shutil.copyfile(os.path.join(templates_dir, "Ubuntu.info"), os.path.join(templates_dir, f"{cust_rel}.info"))
        shutil.copyfile(os.path.join(templates_dir, "Ubuntu.mirrors"), os.path.join(templates_dir, f"{cust_rel}.mirrors"))
    except OSError:
        pass


def messages_safe_read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def recover_chroot_transaction(ctx):
    """Recover stale chroot substitutions without starting a command."""

    return ChrootTransaction(ctx).recover_stale()


def _execute(executor, stage, command, **options):
    if executor is None:
        return run(command, **options)
    return executor.run(stage, tuple(command), **options)


def chroot_run(ctx, *args, executor=None):
    """Port of __chroot__(): prepare, run a command inside the chroot, then clean up."""
    chroot_env = ["env", "HOME=/root", f"LC_ALL={ctx.locales}", f"LANGUAGE={ctx.locales}", f"LANG={ctx.locales}"]

    messages.info("Preparing work environment")
    transaction = ChrootTransaction(ctx)
    result = None
    cleanup_commands_enabled = False
    service_targets = _service_block_targets(ctx)
    if executor is not None:
        executor.begin_chroot_transaction(
            tuple(args),
            tuple(service_targets),
        )

    try:
        with transaction:
            try:
                messages.info("Setting up locale")
                _execute(
                    executor,
                    "chroot-locale",
                    ["chroot", ctx.fs_dir]
                    + chroot_env
                    + ["locale-gen", ctx.locales],
                )

                messages.info("Blocking files")
                transaction.block_files(
                    service_targets,
                    lambda target: _create_service_stub(
                        ctx,
                        chroot_env,
                        target,
                        executor=executor,
                    ),
                )
                cleanup_commands_enabled = True

                if ctx.apt_helper:
                    messages.info("Updating package database")
                    _execute(
                        executor,
                        "chroot-apt-helper",
                        ["chroot", ctx.fs_dir]
                        + chroot_env
                        + ["apt-get", "update", "-qq"],
                    )
                    messages.info("Making sure everything is configured")
                    _execute(
                        executor,
                        "chroot-apt-helper",
                        ["chroot", ctx.fs_dir]
                        + chroot_env
                        + ["dpkg", "--configure", "-a"],
                    )
                    _execute(
                        executor,
                        "chroot-apt-helper",
                        ["chroot", ctx.fs_dir]
                        + chroot_env
                        + ["apt-get", "install", "-f", "-y", "-q"],
                    )

                result = _execute(
                    executor,
                    "chroot-target",
                    ["chroot", ctx.fs_dir] + chroot_env + list(args),
                )
                if result.returncode != 0:
                    messages.warning("chroot has returned exit status")
            finally:
                messages.info("Unblocking files")
                try:
                    transaction.unblock_services()
                except Exception as error:
                    transaction.record_cleanup_failure(
                        "unblock_services",
                        ctx.fs_dir,
                        error,
                    )

                messages.info("Cleaning up work directories")
                if cleanup_commands_enabled:
                    _run_chroot_cleanup_commands(
                        ctx,
                        chroot_env,
                        transaction,
                        executor=executor,
                    )
                _cleanup_work_artifacts(ctx, transaction)
    finally:
        if executor is not None:
            executor.end_chroot_transaction()

    return result


def _service_block_targets(ctx):
    targets = [
        os.path.join(ctx.fs_dir, "sbin/initctl"),
        os.path.join(ctx.fs_dir, "usr/sbin/update-grub"),
    ]
    init_d = os.path.join(ctx.fs_dir, "etc/init.d")
    if os.path.isdir(init_d):
        targets.extend(
            os.path.join(init_d, name)
            for name in os.listdir(init_d)
            if os.path.isfile(os.path.join(init_d, name))
            and not name.endswith(".blocked")
        )
    return targets


def _create_service_stub(ctx, chroot_env, target, executor=None):
    # Mirrors bash's ${f##*FileSystem}, which strips through the last
    # occurrence when the work directory itself contains "FileSystem".
    marker = "FileSystem"
    in_chroot_path = target[
        target.rfind(marker) + len(marker):
    ]
    result = _execute(
        executor,
        "chroot-service-stub",
        ["chroot", ctx.fs_dir]
        + chroot_env
        + ["ln", "-s", "/bin/true", in_chroot_path],
    )
    if result.returncode != 0:
        raise messages.LiveUSBError(
            f"Unable to create service stub for {target}"
        )
    return result


def _run_chroot_cleanup_commands(
    ctx,
    chroot_env,
    transaction,
    executor=None,
):
    cleanup_commands = (
        ["apt-get", "autoremove", "--purge"],
        ["apt-get", "autoclean"],
        ["apt-get", "clean"],
    )
    for command in cleanup_commands:
        full_command = ["chroot", ctx.fs_dir] + chroot_env + command
        try:
            _execute(
                executor,
                "chroot-cleanup",
                full_command,
            )
        except Exception as error:
            transaction.record_cleanup_failure(
                "chroot_cleanup_command",
                " ".join(full_command),
                error,
            )


def _cleanup_work_artifacts(ctx, transaction):
    patterns = (
        "boot/*.bak",
        "var/lib/dpkg/*-old",
        "var/lib/aptitude/*.old",
        "var/cache/debconf/*-old",
        "var/log/*.gz",
        "etc/apt/trusted.gpg~",
        "etc/group-",
        "etc/passwd-",
        "etc/gshadow-",
        "etc/shadow-",
        "var/log/apt/term.log",
    )
    for pattern in patterns:
        for path in glob.glob(os.path.join(ctx.fs_dir, pattern)):
            _remove_cleanup_path(
                path,
                "remove_cleanup_artifact",
                transaction,
            )

    tmp_dir = os.path.join(ctx.fs_dir, "tmp")
    if not os.path.isdir(tmp_dir):
        return
    try:
        names = os.listdir(tmp_dir)
    except OSError as error:
        transaction.record_cleanup_failure(
            "list_temporary_directory",
            tmp_dir,
            error,
        )
        return

    for name in names:
        path = os.path.join(tmp_dir, name)
        if os.path.abspath(path) == os.path.abspath(
            transaction.lock_path
        ):
            continue
        _remove_cleanup_path(
            path,
            "remove_temporary_entry",
            transaction,
        )


def _remove_cleanup_path(path, operation, transaction):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=False)
        else:
            os.remove(path)
    except FileNotFoundError:
        return
    except OSError as error:
        transaction.record_cleanup_failure(
            operation,
            path,
            error,
        )
