"""chroot execution and related helpers, ported from .hidden/common."""

import glob
import os
import shutil

from . import run, run_ok
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


def chroot_run(ctx, *args):
    """Port of __chroot__(): prepare, run a command inside the chroot, then clean up."""
    chroot_env = ["env", "HOME=/root", f"LC_ALL={ctx.locales}", f"LANGUAGE={ctx.locales}", f"LANG={ctx.locales}"]

    messages.info("Preparing work environment")
    shutil.copyfile("/etc/hosts", os.path.join(ctx.fs_dir, "etc/hosts"))
    shutil.copyfile("/etc/resolv.conf", os.path.join(ctx.fs_dir, "etc/resolv.conf"))
    with open(os.path.join(ctx.fs_dir, "etc/debian_chroot"), "w") as fh:
        fh.write("chroot\n")
    mtab = os.path.join(ctx.fs_dir, "etc/mtab")
    if os.path.exists(mtab) or os.path.islink(mtab):
        os.remove(mtab)
    os.symlink("/proc/mounts", mtab)
    lock_dir = os.path.join(ctx.fs_dir, "tmp")
    os.makedirs(lock_dir, exist_ok=True)
    open(os.path.join(lock_dir, "lock_chroot"), "a").close()

    messages.info("Setting up locale")
    run(["chroot", ctx.fs_dir] + chroot_env + ["locale-gen", ctx.locales])

    messages.info("Blocking files")
    block_targets = [
        os.path.join(ctx.fs_dir, "sbin/initctl"),
        os.path.join(ctx.fs_dir, "usr/sbin/update-grub"),
    ]
    init_d = os.path.join(ctx.fs_dir, "etc/init.d")
    if os.path.isdir(init_d):
        block_targets += [
            os.path.join(init_d, name)
            for name in os.listdir(init_d)
            if os.path.isfile(os.path.join(init_d, name)) and not name.endswith(".blocked")
        ]
    for target in block_targets:
        blocked = target + ".blocked"
        if not os.path.exists(target):
            continue
        if not os.path.exists(blocked):
            os.rename(target, blocked)
            in_chroot_path = target[target.find("FileSystem") + len("FileSystem"):]
            run(["chroot", ctx.fs_dir] + chroot_env + ["ln", "-s", "/bin/true", in_chroot_path])
        else:
            messages.warning(f"Blocking of {target} skipped!")

    if ctx.apt_helper:
        messages.info("Updating package database")
        run(["chroot", ctx.fs_dir] + chroot_env + ["apt-get", "update", "-qq"])
        messages.info("Making sure everything is configured")
        run(["chroot", ctx.fs_dir] + chroot_env + ["dpkg", "--configure", "-a"])
        run(["chroot", ctx.fs_dir] + chroot_env + ["apt-get", "install", "-f", "-y", "-q"])

    result = run(["chroot", ctx.fs_dir] + chroot_env + list(args))
    if result.returncode != 0:
        messages.warning("chroot has returned exit status")

    messages.info("Unblocking files")
    unblock_candidates = [
        os.path.join(ctx.fs_dir, "sbin/initctl.blocked"),
        os.path.join(ctx.fs_dir, "usr/sbin/update-grub.blocked"),
    ]
    if os.path.isdir(init_d):
        unblock_candidates += [
            os.path.join(init_d, name) for name in os.listdir(init_d) if name.endswith(".blocked")
        ]
    for blocked in unblock_candidates:
        original = blocked[: -len(".blocked")]
        if os.path.islink(original):
            os.remove(original)
            os.rename(blocked, original)
        elif os.path.isfile(blocked):
            messages.warning(f"{original} has been updated, removing blocked file!")
            os.remove(blocked)
        else:
            messages.warning(f"Unblocking of {blocked} skipped!")

    messages.info("Cleaning up work directories")
    run(["chroot", ctx.fs_dir] + chroot_env + ["apt-get", "autoremove", "--purge"])
    run(["chroot", ctx.fs_dir] + chroot_env + ["apt-get", "autoclean"])
    run(["chroot", ctx.fs_dir] + chroot_env + ["apt-get", "clean"])

    for name in ("debian_chroot", "hosts", "mtab"):
        _silent_remove(os.path.join(ctx.fs_dir, "etc", name))
    for pattern in (
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
    ):
        for path in glob.glob(os.path.join(ctx.fs_dir, pattern)):
            _silent_remove(path)

    tmp_dir = os.path.join(ctx.fs_dir, "tmp")
    if os.path.isdir(tmp_dir):
        for name in os.listdir(tmp_dir):
            path = os.path.join(tmp_dir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                _silent_remove(path)

    return result


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
