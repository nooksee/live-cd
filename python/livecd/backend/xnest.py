"""Port of .hidden/scripts/xnest: test the FileSystem in a nested X session (Xephyr)."""

import os
import re
import shlex
import shutil
import subprocess
import time

from . import mounts, chroot
from .. import messages

_PLACEHOLDER_RE = re.compile(r"%[a-zA-Z]")


def _list_xsessions(ctx):
    xsessions_dir = os.path.join(ctx.fs_dir, "usr/share/xsessions")
    try:
        return sorted(f[:-len(".desktop")] for f in os.listdir(xsessions_dir) if f.endswith(".desktop"))
    except OSError:
        return []


def _choose_session(ctx):
    sessions = _list_xsessions(ctx)
    if not sessions:
        messages.error("No x-session available!")
    if len(sessions) == 1:
        choice = sessions[0]
        messages.extra_warning("There is only one x-session available", choice)
        return choice

    messages.warning("Multiple x-sessions available, choose which one to execute:")
    while True:
        print()
        for name in sessions:
            print(name)
        print()
        messages.question("Which shall it be?")
        choice = input()
        if choice in sessions:
            return choice
        messages.warning("This is not an option!")


def run_xnest(ctx):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.update_distro_name(ctx)
    chroot.check_sources_list()

    choice = _choose_session(ctx)
    desktop_path = os.path.join(ctx.fs_dir, "usr/share/xsessions", f"{choice}.desktop")

    exec_command = None
    with open(desktop_path, errors="replace") as fh:
        for line in fh:
            if line.startswith("Exec="):
                exec_command = line[len("Exec="):].strip()
                break
    if exec_command is None:
        messages.error(f"Unable to parse Exec= from {desktop_path}")

    exec_args = [_PLACEHOLDER_RE.sub("", arg) for arg in shlex.split(exec_command)]

    messages.info("Starting virtual desktop")
    xephyr = subprocess.Popen(["Xephyr", "-wr", "-ac", "-screen", ctx.resolution, ":9"])
    time.sleep(1)

    mounts.allow_local_x_access()
    mounts.mount_sys(ctx)
    mounts.mount_dbus(ctx)
    chroot.chroot_run(ctx, "apt-get", "install", "dbus", "-y", "-f")
    chroot.chroot_run(ctx, "dbus-uuidgen", "--ensure")
    _chroot_run_with_env(ctx, {"DISPLAY": "localhost:9"}, exec_args)
    mounts.umount_sys(ctx)
    mounts.recursive_umount(ctx)
    mounts.block_local_x_access()

    messages.info("Stopping virtual desktop")
    xephyr.terminate()
    try:
        xephyr.wait(timeout=5)
    except subprocess.TimeoutExpired:
        xephyr.kill()

    messages.info("Removing some unwanted directories and files")
    root_dir = os.path.join(ctx.fs_dir, "root")
    for rel in (
        ".kde/share/apps/nepomuk", ".cache", ".dbus", ".thumbnails",
        ".aptitude", ".cups", ".adobe", ".gvfs", ".local/share/gvfs-metadata",
    ):
        shutil.rmtree(os.path.join(root_dir, rel), ignore_errors=True)

    messages.info("Copying changes to default user directory")
    skel_dir = os.path.join(ctx.fs_dir, "etc/skel")
    os.makedirs(skel_dir, exist_ok=True)
    _copy_tree_update(root_dir, skel_dir)

    if choice == "ubuntu":
        compiz_config = os.path.join(root_dir, ".config/compiz-1/compizconfig/config")
        _sed_replace(compiz_config, "profile = Default", "profile = unity")

    messages.info("Adjusting path to /home directory")
    casper_conf = os.path.join(ctx.fs_dir, "etc/casper.conf")
    live_username = None
    with open(casper_conf, errors="replace") as fh:
        for line in fh:
            if line.startswith("export USERNAME="):
                live_username = line[len("export USERNAME="):].strip().strip('"')
                break
    if live_username is None:
        messages.warning("Unable to get user name")
    else:
        _sed_replace_tree(skel_dir, "/home/root/", f"/home/{live_username}")

    messages.info("Adjusting display entries")
    _sed_replace_tree(skel_dir, "localhost:9.0", ":0.0")


def _chroot_run_with_env(ctx, env_vars, args):
    env_args = [f"{k}={v}" for k, v in env_vars.items()]
    chroot.chroot_run(ctx, "env", *env_args, *args)


def _copy_tree_update(src, dst):
    if not os.path.isdir(src):
        return
    for name in os.listdir(src):
        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)
        try:
            if os.path.isdir(src_path) and not os.path.islink(src_path):
                shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
            else:
                shutil.copy2(src_path, dst_path)
        except OSError:
            pass


def _sed_replace(path, old, new):
    try:
        with open(path, errors="replace") as fh:
            content = fh.read()
    except OSError:
        return
    with open(path, "w") as fh:
        fh.write(content.replace(old, new))


def _sed_replace_tree(root, old, new):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            _sed_replace(os.path.join(dirpath, name), old, new)
