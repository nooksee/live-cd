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
from .backend import factory_execution

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
     -r|--rebuild   Disabled; use the factory workflow below
     -q|--qemu      Test Build Image with Desktop Emulator
     -t|--clean     Clean All Temporary Files and Folders

 Factory workflow:

     factory plan rebuild --records-dir ABSOLUTE_DIRECTORY
     factory execute rebuild --grant ABSOLUTE_GRANT_DIRECTORY
     factory recover rebuild --grant ABSOLUTE_GRANT_DIRECTORY

     The legacy -r|--rebuild path is disabled. Complete rebuilds require
     one fresh plan, one atomically consumed grant, and one outcome receipt.

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
    "-q": "qemu", "--qemu": "qemu",
    "-t": "clean", "--clean": "clean",
}

_LEGACY_REBUILD_FLAGS = {"-r", "--rebuild"}


def _factory_usage_error(message):
    messages.extra_error_no_exit("factory command rejected", message)
    return 2


def _factory_main(argv):
    forms = {
        ("plan", "rebuild", "--records-dir"): "plan",
        ("execute", "rebuild", "--grant"): "execute",
        ("recover", "rebuild", "--grant"): "recover",
    }
    if len(argv) != 4:
        return _factory_usage_error("exactly four factory operands are required")
    action = forms.get(tuple(argv[:3]))
    if action is None:
        return _factory_usage_error("factory grammar is invalid")
    path = os.path.abspath(os.fspath(argv[3]))
    if path != argv[3] or os.path.normpath(path) != path:
        return _factory_usage_error("factory path must be normalized and absolute")
    if action in {"execute", "recover"} and os.geteuid() != 0:
        return _factory_usage_error(
            "factory execution and recovery require an already-root process"
        )

    try:
        ctx = Context.load_strict()
        if action == "plan":
            authorization, bundle, receipt = (
                factory_execution.issue_complete_rebuild(
                    ctx,
                    path,
                )
            )
            print(
                receipt.to_json(indent=2)
                if receipt is not None
                else authorization.receipt.to_json(indent=2)
            )
            if bundle is not None:
                print("Grant directory: " + bundle)
            return 0 if authorization.factory_authority_granted else 2
        if action == "execute":
            authorization, bundle, receipt = (
                factory_execution.execute_issued_rebuild(
                    ctx,
                    path,
                )
            )
            print(receipt.to_json(indent=2))
            print("Grant directory: " + bundle)
            return 0 if receipt.payload["status"] == "succeeded" else 2
        bundle, receipt = factory_execution.recover_consumed_rebuild(
            ctx,
            path,
        )
        print(receipt.to_json(indent=2))
        print("Grant directory: " + bundle)
        return 2
    except (OSError, ValueError, messages.LiveUSBError) as error:
        messages.extra_error_no_exit(
            "factory command failed",
            type(error).__name__,
        )
        return 2


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

    if argv[0] == "factory":
        return _factory_main(argv[1:])

    if any(arg in _LEGACY_REBUILD_FLAGS for arg in argv):
        for arg in argv:
            if arg in _LEGACY_REBUILD_FLAGS:
                messages.extra_error_no_exit(
                    "legacy rebuild path is disabled",
                    arg,
                )
        return 2

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
