"""Colored console messages, ported from .hidden/common (bash message helpers)."""

import sys

RESET = "\033[0m"
RED = "\033[1;31m"
BLUE = "\033[1;34m"
YELLOW = "\033[1;33m"

# Whether to use ANSI colors; controlled by the MESSAGES_COLORS config value.
use_colors = True


def set_colors(enabled):
    global use_colors
    use_colors = bool(enabled)


def info(msg):
    if use_colors:
        print(f"[{YELLOW}*{RESET}] {BLUE}{msg}{RESET}")
    else:
        print(f"[*] {msg}")


def extra_info(msg, extra):
    if use_colors:
        print(f"[{YELLOW}*{RESET}] {BLUE}{msg}{RESET}: {YELLOW}{extra}{RESET}")
    else:
        print(f"[*] {msg}: {extra}")


def question(msg):
    if use_colors:
        print(f"[{YELLOW}?{RESET}] {BLUE}{msg}{RESET} ", end="")
    else:
        print(f"[*] {msg} ", end="")


def extra_question(msg, options):
    if use_colors:
        print(f"[{YELLOW}?{RESET}] {BLUE}{msg}{RESET} [{YELLOW}{options}{RESET}] ", end="")
    else:
        print(f"[*] {msg} [{options}] ", end="")


def warning(msg):
    if use_colors:
        print(f"[{RED}!{RESET}] {YELLOW}{msg}{RESET}")
    else:
        print(f"[!] {msg}")


def extra_warning(msg, extra):
    if use_colors:
        print(f"[{RED}!{RESET}] {YELLOW}{msg}{RESET}: {BLUE}{extra}{RESET}")
    else:
        print(f"[!] {msg}: {extra}")


class LiveUSBError(Exception):
    """Raised for a fatal error condition (mirrors ERROR_MESSAGE's exit 2)."""


def error(msg, wait_for_enter=True):
    if use_colors:
        print(f"[{RED}X{RESET}] {YELLOW}{msg}{RESET}")
    else:
        print(f"[X] {msg}")
    if wait_for_enter:
        try:
            input()
        except EOFError:
            pass
    raise LiveUSBError(msg)


def error_no_exit(msg):
    if use_colors:
        print(f"[{RED}X{RESET}] {YELLOW}{msg}{RESET}")
    else:
        print(f"[X] {msg}")
    try:
        input()
    except EOFError:
        pass


def extra_error(msg, extra):
    if use_colors:
        print(f"[{RED}X{RESET}] {YELLOW}{msg}{RESET}: {BLUE}{extra}{RESET}")
    else:
        print(f"[X] {msg}: {extra}")
    try:
        input()
    except EOFError:
        pass
    raise LiveUSBError(f"{msg}: {extra}")


def extra_error_no_exit(msg, extra):
    if use_colors:
        print(f"[{RED}X{RESET}] {YELLOW}{msg}{RESET}: {BLUE}{extra}{RESET}")
    else:
        print(f"[X] {msg}: {extra}")
    try:
        input()
    except EOFError:
        pass


def event_msg(msg):
    """Ported from Func.Event_Msg - logs a GUI-side action to stdout."""
    print(f"[EVENT] {msg}", file=sys.stderr)


def debug_msg(msg):
    """Ported from Func.Debug_Msg - logs a GUI-side debug trace to stdout."""
    print(f"[DEBUG] {msg}", file=sys.stderr)
