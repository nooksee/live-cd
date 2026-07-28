"""Key=value config file helpers, ported from the Gambas Func module
(Get_Str / Replace_Str / Replace_Str_AsIs) and Check.Conf_File.
"""

import os

from . import constants
from . import fsutil
from . import messages


def _quote(value):
    """Roughly mirrors Gambas' Quote(): wrap a value in double quotes."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote(value):
    """Roughly mirrors Gambas' UnQuote(): strip a single layer of quotes."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def get_str(path, key, default):
    """Find a line starting with *key* in *path* and return the remainder.

    If the key is not found, a new line is appended with the quoted default
    value and the default is returned (mirrors Func.Get_Str's autovivify
    behaviour).
    """
    messages.debug_msg(f"SrchFile: {path}")
    messages.debug_msg(f"SrchStr: {key}")
    messages.debug_msg(f"Def: {default}")

    content = fsutil.load_file(path)
    lines = content.split("\n") if content else [""]

    for line in lines:
        if line.startswith(key):
            return _unquote(line[len(key):])

    fsutil.save_file(path, content + "\n" + key + _quote(default))
    return default


def replace_str(path, key, value):
    """Replace (or append) the line starting with *key*, quoting *value*."""
    _replace(path, key, _quote(value))


def replace_str_as_is(path, key, value):
    """Replace (or append) the line starting with *key*, leaving *value* unquoted."""
    _replace(path, key, str(value))


def _replace(path, key, encoded_value):
    messages.debug_msg(f"SrchFile: {path}")
    messages.debug_msg(f"SrchStr: {key}")

    content = fsutil.load_file(path)
    lines = content.split("\n") if content else [""]

    for i, line in enumerate(lines):
        if line.startswith(key):
            lines[i] = key + encoded_value
            fsutil.save_file(path, "\n".join(lines))
            return

    fsutil.save_file(path, content + "\n" + key + encoded_value)


def ensure_config_exists():
    """Port of Check.Conf_File: create /etc/live-usb/default with defaults if missing."""
    messages.event_msg("Checking configuration file")
    if not os.path.exists(constants.CONFIG_FILE):
        messages.warning(
            "Configuration file does not exist, creating one with the default values"
        )
        messages.event_msg("[TRY] Restore configuration file")
        try:
            fsutil.save_file(constants.CONFIG_FILE, constants.DEFAULT_CONFIG_CONTENT)
        except OSError:
            messages.warning("Unable to create configuration file, check your permissions")

    if not os.path.exists(constants.EXCLUDE_FILE):
        try:
            fsutil.save_file(constants.EXCLUDE_FILE, constants.DEFAULT_EXCLUDE_CONTENT)
        except OSError:
            pass


def get_config_str(key, default):
    return get_str(constants.CONFIG_FILE, key, default)


def replace_config_str(key, value):
    replace_str(constants.CONFIG_FILE, key, value)


def get_work_dir():
    return get_config_str("WORK_DIR=", constants.DEFAULT_WORK_DIR)


def get_mount_dir():
    return get_config_str("MOUNT_DIR=", constants.DEFAULT_MOUNT_DIR)


def load_env():
    """Load every setting from the config file into a plain dict, mirroring
    what `source /etc/live-cd/default` gave the original bash scripts (now
    `/etc/live-usb/default`)."""
    ensure_config_exists()
    keys = {
        "WORK_DIR": constants.DEFAULT_WORK_DIR,
        "MOUNT_DIR": constants.DEFAULT_MOUNT_DIR,
        "MESSAGES_COLORS": "1",
        "FORCE_CHROOT": "0",
        "APT_HELPER": "1",
        "RESOLUTION": "1024x768",
        "BOOT_FILES": "0",
        "VRAM": constants.DEFAULT_VRAM,
        "COMPRESSION": "xz",
        "LOCALES": "C",
        "ISO": "",
        "DEB": "",
        "HOOK": "",
        "PIC": "",
    }
    env = {}
    for key, default in keys.items():
        env[key] = get_config_str(f"{key}=", default)
    messages.set_colors(env["MESSAGES_COLORS"] == "1")
    return env
