"""Filesystem paths and default values used throughout LiveUSB Creator."""

import os

ETC_DIR = "/etc/live-usb"
CONFIG_FILE = os.path.join(ETC_DIR, "default")
EXCLUDE_FILE = os.path.join(ETC_DIR, "exclude")
GUI_LOCK_FILE = os.path.join(ETC_DIR, "gui_lock")

DEFAULT_WORK_DIR = "/home/live-usb"
DEFAULT_MOUNT_DIR = "/mnt"
DEFAULT_VRAM = "2048"

DEFAULT_CONFIG_CONTENT = f"""WORK_DIR=/home/live-usb
MOUNT_DIR=/mnt

# Preferences
MESSAGES_COLORS=1
FORCE_CHROOT=0
APT_HELPER=1
RESOLUTION=1024x768
BOOT_FILES=0
VRAM={DEFAULT_VRAM}
COMPRESSION=xz
LOCALES=C

# Saved variables
ISO=
DEB=
HOOK=
PIC=
"""

# File exclude list passed to mksquashfs (equivalent of .hidden/exclude)
DEFAULT_EXCLUDE_CONTENT = """dev/*
cdrom/*
media/*
swapfile
mnt/*
sys/*
proc/*
tmp/*
root/.bash_history
boot/grub/grub.cfg
boot/grub/menu.lst
boot/grub/device.map
var/cache/apt/archives/*.deb
{root,etc/skel,home/*}/.mozilla/*/Cache/*
{root,etc/skel,home/*}/.adobe
{root,etc/skel,home/*}/.macromedia
{root,etc/skel,home/*}/.thumbnails
{root,etc/skel,home/*}/.Trash*
{root,etc/skel,home/*}/.local/share/Trash/*
{root,etc/skel,home/*}/.cache
{root,etc/skel,home/*}/.dbus
{root,etc/skel,home/*}/.gvfs
{root,etc/skel,home/*}/.local/share/gvfs-metadata
{root,etc/skel,home/*}/.bash_history
{root,etc/skel,home/*}/.recently-used
{root,etc/skel,home/*}/.recently-used.xbel
{root,etc/skel,home/*}/.xchat2
{root,etc/skel,home/*}/.cups
{root,etc/skel,home/*}/.kde/share/apps/nepomuk
{root,etc/skel,home/*}/.aptitude
"""

TERMINAL_EMULATORS = [
    "/usr/bin/gnome-terminal",
    "/usr/bin/mate-terminal",
    "/usr/bin/konsole",
    "/usr/bin/xfce4-terminal",
    "/usr/bin/terminal",
    "/usr/bin/lxterminal",
    "/usr/bin/terminator",
    "/usr/bin/sakura",
    "/usr/bin/yakuake",
    "/usr/bin/urxterm",
    "/usr/bin/aterm",
    "/usr/bin/Eterm",
    "/usr/bin/xterm",
]

TEXT_EDITORS = [
    "/usr/bin/gedit",
    "/usr/bin/pluma",
    "/usr/bin/kate",
    "/usr/bin/kwrite",
    "/usr/bin/medit",
    "/usr/bin/nedit",
    "/usr/bin/mousepad",
    "/usr/bin/leafpad",
    "/usr/bin/geany",
    "/usr/bin/emacs",
    "/usr/bin/bluefish",
    "/usr/bin/ultraedit",
    "/usr/bin/scite",
    "/usr/bin/vim",
    "/usr/bin/vi",
    "/usr/bin/nano",
    "/usr/bin/pico",
]

# Editors that only work inside a terminal
TERMINAL_ONLY_EDITORS = {"/usr/bin/vim", "/usr/bin/vi", "/usr/bin/nano", "/usr/bin/pico"}

FILE_MANAGERS = [
    "/usr/bin/nautilus",
    "/usr/bin/caja",
    "/usr/bin/dolphin",
    "/usr/bin/pcmanfm",
    "/usr/bin/xfe",
    "/usr/bin/worker",
    "/usr/bin/gentoo",
    "/usr/bin/thunar",
    "/usr/bin/filerunner",
    "/usr/bin/krusader",
    "/usr/bin/rox-filer",
]
