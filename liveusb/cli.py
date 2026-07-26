"""Command line entrypoint, ported from .hidden/live-cd.sh.

Each recognised flag runs the equivalent of one of the original
/etc/live-cd/scripts/* bash scripts. Multiple flags may be combined in a
single invocation, e.g. `live-usb -e -r -q`, exactly like the original.
"""

import datetime
import os
import shlex
import subprocess
import sys

from . import __version__, config, messages
from .backend import Context
from .backend import cdimage, chroot_shell, clean, deb, extract, gui_install, hook, pkgm, qemu, rebuild, xnest

USAGE = """
 Main options:

     -e|--extract   Extract Image File
     -i|--cdimage   Extract Image File from Disk
     -c|--chroot    chroot into the FileSystem
     -x|--xnest     Execute Nested X-Session
     -p|--pkgm      Execute Package Manager
     -d|--deb       Install Debian Package
     -k|--hook      Execute Hook
     -g|--gui       Install Desktop Environment
     -r|--rebuild   Rebuild Image File
     -q|--qemu      Test Build Image with Desktop Emulator
     -t|--clean     Clean All Temporary Files and Folders

 Other options:

     -h|--help      Display This Message
     -v|--version   Show the Current Version and More
"""

VERSION_TEXT = f"""
LiveUSB Creator {__version__}

Development status: pre-alpha

Links:

  Homepage: https://github.com/nooksee/live-usb
  Documentation: https://github.com/nooksee/live-usb#readme
  Issues: https://github.com/nooksee/live-usb/issues


Project creator and maintainer:
  Kevin Thomas (nooksee)

Historical and AI collaboration credits:
  https://github.com/nooksee/live-usb/blob/after-hours/python-modernization/CONTRIBUTORS.md

License:
  GPL-2.0-or-later
"""

ACTIONS = {
    "extract": lambda ctx: extract.run_extract(ctx),
    "cdimage": lambda ctx: cdimage.run_cdimage(ctx),
    "chroot": lambda ctx: chroot_shell.run_chroot(ctx),
    "xnest": lambda ctx: xnest.run_xnest(ctx),
    "pkgm": lambda ctx: pkgm.run_pkgm(ctx),
    "deb": lambda ctx: deb.run_deb(ctx),
    "hook": lambda ctx: hook.run_hook(ctx),
    "gui": lambda ctx: gui_install.run_gui_install(ctx),
    "rebuild": lambda ctx: rebuild.run_rebuild(ctx),
    "qemu": lambda ctx: qemu.run_qemu(ctx),
    "clean": lambda ctx: clean.run_clean(ctx),
}

FLAG_TO_ACTION = {
    "-e": "extract", "--extract": "extract",
    "-i": "cdimage", "--cdimage": "cdimage",
    "-c": "chroot", "--chroot": "chroot",
    "-x": "xnest", "--xnest": "xnest",
    "-p": "pkgm", "--pkgm": "pkgm",
    "-d": "deb", "--deb": "deb",
    "-k": "hook", "--hook": "hook",
    "-g": "gui", "--gui": "gui",
    "-r": "rebuild", "--rebuild": "rebuild",
    "-q": "qemu", "--qemu": "qemu",
    "-t": "clean", "--clean": "clean",
}


def root_it(action):
    """Port of Root_it(): run *action* as root, prompting via `su` if needed."""
    label = action
    start = datetime.datetime.now().strftime("%X/%d-%m-%Y")
    messages.extra_info(f"Executing {label} at", start)

    if os.geteuid() != 0:
        messages.warning("You are not root! Prompting for password!")
        inner_argv = [sys.executable, "-m", "liveusb.cli", f"--{action}"]
        inner_cmd = " ".join(shlex.quote(part) for part in inner_argv)
        result = subprocess.run(["su", "-c", inner_cmd])
        if result.returncode != 0:
            sys.exit(result.returncode)
    else:
        ctx = Context.load()
        try:
            ACTIONS[action](ctx)
        except messages.LiveUSBError as exc:
            messages.warning(f"{label} failed: {exc}")
            sys.exit(2)

    finish = datetime.datetime.now().strftime("%X/%d-%m-%Y")
    messages.extra_info(f"Finished {label} at", finish)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    if not argv:
        print(USAGE)
        return 0

    informational_flags = {"-v", "--version", "-h", "--help"}
    valid_flags = informational_flags | set(FLAG_TO_ACTION)
    invalid_args = [arg for arg in argv if arg not in valid_flags]
    if invalid_args:
        for arg in invalid_args:
            messages.extra_error_no_exit("unrecognised argument", arg)
        return 2

    if any(arg in FLAG_TO_ACTION for arg in argv):
        # Operational requests retain the original up-front config load.
        # Pure help, version, and rejected requests remain read-only and do
        # not require permission to create /etc/live-usb.
        config.load_env()

    ran_something = False
    for arg in argv:
        if arg in ("-v", "--version"):
            print(VERSION_TEXT)
            ran_something = True
        elif arg in ("-h", "--help"):
            print(USAGE)
            ran_something = True
        elif arg in FLAG_TO_ACTION:
            root_it(FLAG_TO_ACTION[arg])
            ran_something = True
        else:
            messages.extra_error_no_exit("unrecognised argument", arg)
            return 2

    return 0 if ran_something else 1


if __name__ == "__main__":
    sys.exit(main())
