"""Port of FMain.class / FMain.form: the main LiveUSB Creator window."""

import os
import subprocess

from .gtkcompat import Gtk, GdkPixbuf

from . import checks
from .. import config, constants, fsutil, messages, resources


def _load_icon(name, size=24):
    path = resources.icon_path(name)
    if path is None:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
        return Gtk.Image.new_from_pixbuf(pixbuf)
    except Exception:
        return None


class MainWindow:
    def __init__(self, on_close=None):
        self._on_close = on_close
        self.work_dir = config.get_work_dir()

        self.window = Gtk.Window(title="LiveUSB Creator")
        self.window.set_border_width(6)
        self.window.set_resizable(False)
        self.window.connect("destroy", self._on_destroy)

        icon_path = resources.app_icon_path()
        if icon_path:
            try:
                self.window.set_icon_from_file(icon_path)
            except Exception:
                pass

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.window.add(outer)

        outer.pack_start(self._build_menu_bar(), False, False, 0)
        outer.pack_start(self._build_toolbar(), False, False, 0)
        outer.pack_start(self._build_distribution_frame(), True, True, 0)

        self.window.show_all()
        self._form_open()

    # -- construction -----------------------------------------------------

    def _menu_item(self, menu, label, callback, sensitive=True):
        item = Gtk.MenuItem(label=label)
        if callback:
            item.connect("activate", callback)
        item.set_sensitive(sensitive)
        menu.append(item)
        return item

    def _build_menu_bar(self):
        menubar = Gtk.MenuBar()

        main_menu = Gtk.Menu()
        main_item = Gtk.MenuItem(label="Main")
        main_item.set_submenu(main_menu)
        self._menu_item(main_menu, "Get Ubuntu", self.on_downloader)
        self._menu_item(main_menu, "Image File From Disk", self.on_extract_iso)
        self._menu_item(main_menu, "Settings", self.on_settings)
        self._menu_item(main_menu, "Quit", self.on_quit)
        menubar.append(main_item)

        self.extras_menu = Gtk.Menu()
        extras_item = Gtk.MenuItem(label="Extras")
        extras_item.set_submenu(self.extras_menu)
        extras_item.set_sensitive(False)
        self._menu_item(self.extras_menu, "Advanced Settings", self.on_tweaks)
        self._menu_item(self.extras_menu, "Execute Hook", self.on_exec_hook)
        self._menu_item(self.extras_menu, "Install Desktop Environment", self.on_install_gui)
        self._menu_item(self.extras_menu, "Customize Bootloader", self.on_syslinux)
        self._menu_item(self.extras_menu, "Customize Boot Manager", self.on_grub2)
        self._menu_item(self.extras_menu, "List Installed Packages", self.on_list_packages)
        menubar.append(extras_item)
        self.extras_menu_item = extras_item

        self.directories_menu = Gtk.Menu()
        dirs_item = Gtk.MenuItem(label="Directories")
        dirs_item.set_submenu(self.directories_menu)
        dirs_item.set_sensitive(False)
        for label, rel in [
            ("FileSystem", "/FileSystem"),
            ("FileSystem/boot", "/FileSystem/boot"),
            ("FileSystem/etc", "/FileSystem/etc"),
            ("FileSystem/etc/default", "/FileSystem/etc/default"),
            ("FileSystem/etc/skel", "/FileSystem/etc/skel"),
            ("FileSystem/root", "/FileSystem/root"),
            ("FileSystem/usr", "/FileSystem/usr"),
            ("FileSystem/usr/share", "/FileSystem/usr/share"),
            ("ISO", "/ISO"),
            ("ISO/isolinux", "/ISO/isolinux"),
        ]:
            self._menu_item(self.directories_menu, label, self._browse_handler(rel))
        menubar.append(dirs_item)
        self.directories_menu_item = dirs_item

        self.files_menu = Gtk.Menu()
        files_item = Gtk.MenuItem(label="Files")
        files_item.set_submenu(self.files_menu)
        files_item.set_sensitive(False)
        for label, rel in [
            ("/etc/casper.conf", "/FileSystem/etc/casper.conf"),
            ("/etc/lsb-release", "/FileSystem/etc/lsb-release"),
            ("/etc/os-release", "/FileSystem/etc/os-release"),
            ("/etc/default/grub", "/FileSystem/etc/default/grub"),
            ("/etc/grub.d/05_debian_theme", "/FileSystem/etc/grub.d/05_debian_theme"),
            ("/etc/gtk-3.0/settings.ini", "/FileSystem/etc/gtk-3.0/settings.ini"),
            ("/etc/update-motd.d/10-help-text", "/FileSystem/etc/update-motd.d/10-help-text"),
            ("/ISO/boot/grub/grub.cfg", "/ISO/boot/grub/grub.cfg"),
        ]:
            self._menu_item(self.files_menu, label, self._edit_handler(rel))
        menubar.append(files_item)
        self.files_menu_item = files_item

        help_menu = Gtk.Menu()
        help_item = Gtk.MenuItem(label="Help")
        help_item.set_submenu(help_menu)
        self._menu_item(help_menu, "Credits", self.on_credits)
        self._menu_item(help_menu, "License", self.on_license)
        self._menu_item(help_menu, "About", self.on_about)
        menubar.append(help_item)

        return menubar

    def _icon_button(self, icon_name, tooltip, callback, sensitive=True):
        button = Gtk.Button()
        button.set_relief(Gtk.ReliefStyle.NONE)
        image = _load_icon(icon_name)
        if image:
            button.set_image(image)
        else:
            button.set_label(tooltip)
        button.set_tooltip_text(tooltip)
        button.set_sensitive(sensitive)
        button.connect("clicked", callback)
        return button

    def _build_toolbar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        self.use_image_btn = self._icon_button(
            "image.png", "Select image file to be used as the base for distribution", self.on_use_image
        )
        self.edit_sources_btn = self._icon_button(
            "edit.png", "Edit sources.list via text editor", self.on_edit_sources, sensitive=False
        )
        self.terminal_btn = self._icon_button(
            "terminal.png", "Modify distribution using chroot terminal", self.on_terminal, sensitive=False
        )
        self.install_deb_btn = self._icon_button(
            "deb.png", "Install debian package", self.on_install_deb, sensitive=False
        )
        self.archive_btn = self._icon_button(
            "synaptic.png", "Run package manager to upgrade, install or purge packages",
            self.on_archive, sensitive=False,
        )
        self.desktop_btn = self._icon_button(
            "desktop.png", "Modify distribution using virtual desktop", self.on_desktop, sensitive=False
        )

        for widget in (
            self.use_image_btn, self.edit_sources_btn, self.terminal_btn,
            self.install_deb_btn, self.archive_btn, self.desktop_btn,
        ):
            box.pack_start(widget, False, False, 0)

        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        self.build_iso_btn = self._icon_button(
            "build.png", "Rebuild distribution", self.on_build_iso, sensitive=False
        )
        self.qemu_btn = self._icon_button(
            "computer.png", "Test build using desktop emulator", self.on_qemu, sensitive=False
        )
        self.clean_btn = self._icon_button(
            "clean.png", "Clean all temp files and directories", self.on_clean, sensitive=False
        )
        for widget in (self.build_iso_btn, self.qemu_btn, self.clean_btn):
            box.pack_start(widget, False, False, 0)

        return box

    def _build_distribution_frame(self):
        frame = Gtk.Frame(label=" Distribution")
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        grid.set_border_width(6)
        frame.add(grid)

        self.distname_entry = Gtk.Entry(max_length=16, sensitive=False)
        self.liveusb_user_entry = Gtk.Entry(max_length=16, sensitive=False)
        self.hostname_entry = Gtk.Entry(max_length=16, sensitive=False)
        self.version_entry = Gtk.Entry(sensitive=False)
        self.releasenotesurl_entry = Gtk.Entry(sensitive=False)

        grid.attach(Gtk.Label(label="Name", xalign=0), 0, 0, 1, 1)
        grid.attach(self.distname_entry, 0, 1, 1, 1)
        grid.attach(Gtk.Label(label="User", xalign=0), 1, 0, 1, 1)
        grid.attach(self.liveusb_user_entry, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Host", xalign=0), 0, 2, 1, 1)
        grid.attach(self.hostname_entry, 0, 3, 1, 1)
        grid.attach(Gtk.Label(label="Version", xalign=0), 1, 2, 1, 1)
        grid.attach(self.version_entry, 1, 3, 1, 1)
        grid.attach(Gtk.Label(label="Release Notes", xalign=0), 0, 4, 2, 1)
        grid.attach(self.releasenotesurl_entry, 0, 5, 2, 1)

        self.distname_entry.connect("changed", self.on_distname_change)
        self.hostname_entry.connect("changed", self.on_hostname_change)
        self.liveusb_user_entry.connect("changed", self.on_liveusb_user_change)
        self.version_entry.connect("changed", self.on_version_change)
        self.releasenotesurl_entry.connect("changed", self.on_releasenotesurl_change)

        return frame

    # -- helpers ------------------------------------------------------------

    def _browse_handler(self, rel_path):
        return lambda _widget: fsutil.browse_dir(os.path.join(self.work_dir, rel_path.lstrip("/")))

    def _edit_handler(self, rel_path):
        return lambda _widget: fsutil.edit_file(os.path.join(self.work_dir, rel_path.lstrip("/")))

    def _run_in_terminal(self, flag):
        term = fsutil.use_term()
        if term is None:
            return
        cli = resources.find_cli_executable()
        subprocess.run([term, "-e", f"{cli} {flag}"])

    def _enable_stuff(self):
        messages.event_msg("Enabling widgets")
        for widget in (
            self.edit_sources_btn, self.install_deb_btn, self.terminal_btn,
            self.build_iso_btn, self.clean_btn, self.archive_btn,
            self.distname_entry, self.hostname_entry, self.liveusb_user_entry,
            self.version_entry, self.releasenotesurl_entry,
        ):
            widget.set_sensitive(True)
        for widget in (self.extras_menu_item, self.directories_menu_item, self.files_menu_item):
            widget.set_sensitive(True)

    def _disable_stuff(self):
        messages.event_msg("Disabling widgets")
        for widget in (
            self.edit_sources_btn, self.install_deb_btn, self.archive_btn,
            self.terminal_btn, self.build_iso_btn, self.clean_btn,
            self.desktop_btn, self.distname_entry, self.hostname_entry,
            self.liveusb_user_entry, self.version_entry, self.releasenotesurl_entry,
        ):
            widget.set_sensitive(False)
        for widget in (self.extras_menu_item, self.directories_menu_item, self.files_menu_item):
            widget.set_sensitive(False)

    # -- Check.module equivalents --------------------------------------------

    def refresh_iso_state(self):
        messages.event_msg("Checking if image file exists")
        if checks.built_iso_path(self.work_dir):
            self.qemu_btn.set_sensitive(True)

    def refresh_x_session_state(self):
        messages.event_msg("Checking for x-session links")
        self.desktop_btn.set_sensitive(checks.x_session_available(self.work_dir))

    def refresh_pkg_manager_state(self):
        messages.event_msg("Searching for package manager")
        self.archive_btn.set_sensitive(checks.pkg_manager_available(self.work_dir))

    def refresh_existence(self):
        messages.event_msg("Checking for essential directories and files")
        status = checks.essential_status(self.work_dir)

        if status == checks.ESSENTIAL_STATUS_MISSING:
            self._disable_stuff()
            return

        if status == checks.ESSENTIAL_STATUS_CORRUPT:
            self._show_error("Some important directories and or files are missing!")
            self.clean_btn.set_sensitive(True)
            return

        if status == checks.ESSENTIAL_STATUS_INCOMPLETE:
            fs_dir = os.path.join(self.work_dir, "FileSystem")
            self._show_error(
                f"{fs_dir}/etc/casper.conf or {fs_dir}/etc/lsb-release\n"
                "are deleted but are very essential for setting up some configuration.\n"
                "Create them using a text editor or clean and start all over again!"
            )
            self._disable_stuff()
            self.clean_btn.set_sensitive(True)
            return

        self._enable_stuff()

        casper_conf = os.path.join(self.work_dir, "FileSystem/etc/casper.conf")
        lsb_release = os.path.join(self.work_dir, "FileSystem/etc/lsb-release")

        self.hostname_entry.set_text(config.get_str(casper_conf, "export HOST=", "host"))
        self.liveusb_user_entry.set_text(config.get_str(casper_conf, "export USERNAME=", "live"))
        self.version_entry.set_text(config.get_str(lsb_release, "DISTRIB_RELEASE=", "13.10"))
        self.distname_entry.set_text(config.get_str(lsb_release, "DISTRIB_ID=", "Custom"))

        release_notes_path = os.path.join(self.work_dir, "ISO/.disk/release_notes_url")
        url = fsutil.load_file(release_notes_path).strip()
        self.releasenotesurl_entry.set_text(url or "http://www.ubuntu.com/getubuntu/releasenotes")

        self.refresh_iso_state()
        self.refresh_x_session_state()
        self.refresh_pkg_manager_state()

    # -- dialogs --------------------------------------------------------------

    def _show_error(self, text):
        dialog = Gtk.MessageDialog(
            transient_for=self.window, flags=0, message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK, text=text,
        )
        dialog.run()
        dialog.destroy()

    def _show_warning(self, text):
        dialog = Gtk.MessageDialog(
            transient_for=self.window, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK, text=text,
        )
        dialog.run()
        dialog.destroy()

    def _pick_open_file(self, title, initial_path, patterns_and_names):
        dialog = Gtk.FileChooserDialog(
            title=title, transient_for=self.window, action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        if initial_path and os.path.exists(initial_path):
            dialog.set_filename(initial_path)
        else:
            dialog.set_current_folder("/home")
        for patterns, name in patterns_and_names:
            filt = Gtk.FileFilter()
            filt.set_name(name)
            for pattern in patterns:
                filt.add_pattern(pattern)
            dialog.add_filter(filt)

        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return path

    # -- Form_Open / Form_Close ------------------------------------------------

    def _form_open(self):
        messages.event_msg("Checking if LiveUSB Creator is locked")
        if os.path.exists(constants.GUI_LOCK_FILE):
            self._show_warning("Another instance of LiveUSB Creator is already running!")
            self.window.destroy()
            return

        messages.event_msg("Locking LiveUSB Creator")
        try:
            fsutil.save_file(constants.GUI_LOCK_FILE, "")
        except OSError:
            pass

        config.ensure_config_exists()
        self.refresh_existence()

    def _on_destroy(self, _widget):
        try:
            os.remove(constants.GUI_LOCK_FILE)
        except OSError:
            pass
        if self._on_close:
            self._on_close()

    # -- signal handlers --------------------------------------------------------

    def on_use_image(self, _widget):
        iso = config.get_config_str("ISO=", "")
        path = self._pick_open_file("Please select an image file", iso, [(["*.iso"], "ISO Images")])
        if path is None:
            return
        config.replace_config_str("ISO=", path)
        messages.event_msg("Extracting image file")
        self._run_in_terminal("--extract")
        self.refresh_existence()

    def on_edit_sources(self, _widget):
        fsutil.edit_file(os.path.join(self.work_dir, "FileSystem/etc/apt/sources.list"))

    def on_desktop(self, _widget):
        self._run_in_terminal("--xnest")
        self.refresh_pkg_manager_state()

    def on_terminal(self, _widget):
        self._run_in_terminal("--chroot")
        self.refresh_x_session_state()
        self.refresh_pkg_manager_state()

    def on_build_iso(self, _widget):
        fields = [
            self.distname_entry, self.hostname_entry, self.liveusb_user_entry,
            self.version_entry, self.releasenotesurl_entry,
        ]
        if any(field.get_text() == "" for field in fields):
            self._show_warning("One or more of the configuration fields are blank!")
        else:
            messages.event_msg("Rebuilding image file")
            self._run_in_terminal("--rebuild")
        self.refresh_iso_state()

    def on_clean(self, _widget):
        messages.event_msg("Cleaning work directories")
        self._run_in_terminal("--clean")
        self._disable_stuff()

    def on_install_deb(self, _widget):
        deb = config.get_config_str("DEB=", "")
        path = self._pick_open_file("Please select a debian package", deb, [(["*.deb"], "Debian Packages")])
        if path is None:
            return
        config.replace_config_str("DEB=", path)
        messages.event_msg("Installing debian package")
        self._run_in_terminal("--deb")
        self.refresh_x_session_state()
        self.refresh_pkg_manager_state()

    def on_archive(self, _widget):
        messages.event_msg("Launching package manager")
        self._run_in_terminal("--pkgm")
        self.refresh_x_session_state()

    def on_qemu(self, _widget):
        messages.event_msg("Launching desktop emulator")
        self._run_in_terminal("--qemu")

    def on_distname_change(self, entry):
        value = entry.get_text().strip()
        version = self.version_entry.get_text().strip()
        messages.event_msg("DistName changed")
        config.replace_str_as_is(os.path.join(self.work_dir, "FileSystem/etc/lsb-release"), "DISTRIB_ID=", value)
        config.replace_str_as_is(os.path.join(self.work_dir, "FileSystem/etc/os-release"), "NAME=", value)
        fsutil.save_file(os.path.join(self.work_dir, "FileSystem/etc/issue"), f"{value} {version} \\n \\l")
        fsutil.save_file(os.path.join(self.work_dir, "FileSystem/etc/issue.net"), f"{value} {version}")

    def on_hostname_change(self, entry):
        value = entry.get_text().strip().lower()
        messages.event_msg("HostName changed")
        config.replace_str(os.path.join(self.work_dir, "FileSystem/etc/casper.conf"), "export HOST=", value)
        config.replace_str(os.path.join(self.work_dir, "FileSystem/etc/casper.conf"), "export FLAVOUR=", "custom")

    def on_liveusb_user_change(self, entry):
        value = entry.get_text().strip().lower()
        messages.event_msg("LiveUSB_User changed")
        config.replace_str(os.path.join(self.work_dir, "FileSystem/etc/casper.conf"), "export USERNAME=", value)
        config.replace_str(os.path.join(self.work_dir, "FileSystem/etc/casper.conf"), "export FLAVOUR=", "custom")

    def on_version_change(self, entry):
        value = entry.get_text().strip()
        distname = self.distname_entry.get_text().strip()
        messages.event_msg("Version changed")
        config.replace_str_as_is(os.path.join(self.work_dir, "FileSystem/etc/lsb-release"), "DISTRIB_RELEASE=", value)
        config.replace_str_as_is(os.path.join(self.work_dir, "FileSystem/etc/os-release"), "VERSION_ID=", value)
        fsutil.save_file(os.path.join(self.work_dir, "FileSystem/etc/issue"), f"{distname} {value} \\n \\l")
        fsutil.save_file(os.path.join(self.work_dir, "FileSystem/etc/issue.net"), f"{distname} {value}")

    def on_releasenotesurl_change(self, entry):
        messages.event_msg("ReleaseNotesURL changed")
        fsutil.save_file(os.path.join(self.work_dir, "ISO/.disk/release_notes_url"), entry.get_text().strip())

    def on_quit(self, _widget):
        self.window.destroy()

    def on_settings(self, _widget):
        from .settings_window import SettingsWindow
        settings = SettingsWindow(self.window, on_close=self._on_settings_closed)
        settings.set_work_dir_locked(self.build_iso_btn.get_sensitive())

    def _on_settings_closed(self):
        self.work_dir = config.get_work_dir()

    def on_extract_iso(self, _widget):
        messages.event_msg("Extracting image file from disk")
        self._run_in_terminal("--cdimage")

    def on_exec_hook(self, _widget):
        hook = config.get_config_str("HOOK=", "")
        path = self._pick_open_file("Please select hook file", hook, [])
        if path is None:
            return
        messages.event_msg("Executing hook")
        config.replace_config_str("HOOK=", path)
        self._run_in_terminal("--hook")
        self.refresh_x_session_state()
        self.refresh_pkg_manager_state()

    def on_install_gui(self, _widget):
        messages.event_msg("Installing desktop environment")
        self._run_in_terminal("--gui")
        self.refresh_x_session_state()
        self.refresh_pkg_manager_state()

    def on_tweaks(self, _widget):
        from .tweaks_window import TweaksWindow
        TweaksWindow(self.window)

    def on_list_packages(self, _widget):
        from .packages_window import PackagesWindow
        PackagesWindow(self.window)

    def on_syslinux(self, _widget):
        from .syslinux_window import SysLinuxWindow
        SysLinuxWindow(self.window)

    def on_grub2(self, _widget):
        from .grub2_window import Grub2Window
        Grub2Window(self.window)

    def on_credits(self, _widget):
        from .credits_window import CreditsWindow
        CreditsWindow(self.window)

    def on_license(self, _widget):
        from .license_window import LicenseWindow
        LicenseWindow(self.window)

    def on_about(self, _widget):
        from .about_window import AboutWindow
        AboutWindow(self.window)

    def on_downloader(self, _widget):
        from .downloader_window import DownloaderWindow
        self.window.hide()
        DownloaderWindow(on_close=self.window.show)
