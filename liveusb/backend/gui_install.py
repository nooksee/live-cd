"""Port of .hidden/scripts/gui: install a desktop environment into the FileSystem."""

from . import mounts, chroot
from .. import messages

DESKTOPS = {
    "1": ("Ubuntu", "xserver-xorg xinit ubuntu-desktop"),
    "2": ("Xubuntu", "xserver-xorg xinit xubuntu-desktop"),
    "3": ("Lubuntu", "xserver-xorg xinit lubuntu-desktop"),
    "4": ("Kubuntu", "xserver-xorg xinit kubuntu-desktop"),
}


def run_gui_install(ctx, choice=None):
    mounts.check_fs_dir(ctx)
    mounts.check_lock(ctx)
    chroot.check_sources_list()

    messages.warning("ONLY THE BARE MINIMUM SET OF PACKAGES WILL BE INSTALLED!")

    gui_packages = None
    while gui_packages is None:
        if choice is None:
            print("\n\t1. Ubuntu\n\t2. Xubuntu\n\t3. Lubuntu\n\t4. Kubuntu\n")
            messages.extra_question("Which desktop environment do you wish to install?", "1-4")
            choice = input()
        if choice in DESKTOPS:
            gui_packages = DESKTOPS[choice][1]
        else:
            messages.warning("This is not an option!")
            choice = None

    mounts.mount_sys(ctx)
    mounts.mount_dbus(ctx)
    chroot.chroot_run(ctx, "apt-get", "install", "dbus", "-f", "-y")
    chroot.chroot_run(ctx, "dbus-uuidgen", "--ensure")
    chroot.chroot_run(ctx, "apt-get", "install", "-y", *gui_packages.split(), "--no-install-recommends")
    mounts.umount_sys(ctx)
    mounts.recursive_umount(ctx)
