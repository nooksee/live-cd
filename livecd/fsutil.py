"""Generic filesystem helpers, ported from the Gambas Func module."""

import os
import shutil
import subprocess

from . import constants
from . import messages


def load_file(path):
    """Return the contents of *path*, or "" if it does not exist/can't be read."""
    messages.debug_msg(f"Loading file: {path}")
    if not os.path.exists(path):
        return ""
    if not os.access(path, os.R_OK):
        messages.warning(f"{path} is not readable!")
        return ""
    with open(path, "r", errors="replace") as fh:
        return fh.read()


def save_file(path, content):
    """Write *content* to *path*, creating it if necessary."""
    messages.debug_msg(f"Saving file: {path}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def find_first_existing(candidates):
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def use_term():
    """Find an available terminal emulator (Func.Use_Term)."""
    messages.event_msg("Searching for terminal emulator")
    term = find_first_existing(constants.TERMINAL_EMULATORS)
    if term is None:
        messages.warning(
            "Unable to detect terminal emulator! Supported are: Gnome-terminal, "
            "Konsole, XFCE4-terminal, LXTerminal, Terminator, Sakura, Yakuake, "
            "urxterm, Eterm, xterm and aterm"
        )
    return term


def find_editor():
    """Find an available text editor (part of Func.Edit_File)."""
    messages.event_msg("Searching for text editor")
    editor = find_first_existing(constants.TEXT_EDITORS)
    if editor is None:
        messages.warning("Unable to detect text editor!")
    return editor


def edit_file(path):
    """Open *path* in a detected text editor (Func.Edit_File)."""
    editor = find_editor()
    if editor is None:
        return
    messages.debug_msg(f"Editing file: {path}")
    try:
        if editor in constants.TERMINAL_ONLY_EDITORS:
            term = use_term()
            if term is None:
                return
            subprocess.Popen([term, "-e", editor, path])
        else:
            subprocess.Popen([editor, path])
    except OSError:
        messages.warning(f"Unable to edit\n\nFile: {path}\nwith text editor: {editor}")


def find_file_manager():
    return find_first_existing(constants.FILE_MANAGERS)


def browse_dir(path):
    """Open *path* in a detected file manager (Func.Browse_Dir)."""
    messages.event_msg(f"Browsing directory: {path}")
    if not os.path.exists(path):
        messages.warning("This directory does not exist!")
        return
    manager = find_file_manager()
    if manager is None:
        messages.warning(
            "No supported file manager was detected!\nSupported are:\n\n"
            "Nautilus, Dolphin, PCManFM, Thunar, XFE, Worker, Gentoo, "
            "FileRunner, Krusader and ROX-Filer"
        )
        return
    subprocess.Popen([manager, path])


def which(cmd):
    return shutil.which(cmd)
