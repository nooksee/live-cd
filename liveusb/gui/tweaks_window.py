"""Port of FTweaks.class / FTweaks.form: advanced distribution settings."""

import os

from .gtkcompat import Gtk

from .. import config, fsutil, messages

SHELL_CANDIDATES = [
    "ash", "sh", "csh", "ksh", "mksh", "tcsh", "bash", "dash", "psh", "zsh", "yash",
]

TTY_COUNTS = [str(n) for n in range(1, 12)]


def _combo():
    return Gtk.ComboBoxText()


def _select(combo, value):
    model = combo.get_model()
    for i, row in enumerate(model):
        if row[0] == value:
            combo.set_active(i)
            return
    if len(model) > 0:
        combo.set_active(0)


class TweaksWindow:
    def __init__(self, parent):
        config.ensure_config_exists()
        self.work_dir = config.get_work_dir()
        self.fs_dir = os.path.join(self.work_dir, "FileSystem")

        self.window = Gtk.Window(title="Advanced Settings")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_resizable(False)
        self.window.set_border_width(8)

        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        self.window.add(grid)
        row = 0

        self.apt_recommends_check = Gtk.CheckButton(label="Suggested and Recommended Packages")
        apt_conf = os.path.join(self.fs_dir, "etc/apt/apt.conf")
        self.apt_recommends_check.set_active(not os.path.exists(apt_conf))
        self.apt_recommends_check.connect("toggled", self.on_apt_recommends_toggled)
        grid.attach(self.apt_recommends_check, 0, row, 2, 1)
        row += 1

        self.framebuffer_check = Gtk.CheckButton(label="Use Framebuffer")
        casper_hook = os.path.join(self.fs_dir, "usr/share/initramfs-tools/conf-hooks.d/casper")
        if os.path.exists(casper_hook):
            frame = config.get_str(casper_hook, "FRAMEBUFFER=", "y")
            self.framebuffer_check.set_active(frame == "y")
        else:
            messages.warning(
                "Casper hook file does not exist! You will not be able to choose "
                "whether or not to use framebuffer."
            )
            self.framebuffer_check.set_sensitive(False)
        self.framebuffer_check.connect("toggled", self.on_framebuffer_toggled)
        grid.attach(self.framebuffer_check, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label="TimeZone", xalign=0), 0, row, 1, 1)
        self.zone_combo = _combo()
        self.time_combo = _combo()
        self._populate_zones()
        self.zone_combo.connect("changed", self.on_zone_changed)
        self.time_combo.connect("changed", self.on_time_changed)
        grid.attach(self.zone_combo, 1, row, 1, 1)
        grid.attach(self.time_combo, 2, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Default Shell", xalign=0), 0, row, 1, 1)
        self.shell_combo = _combo()
        self._populate_shells()
        self.shell_combo.connect("changed", self.on_shell_changed)
        grid.attach(self.shell_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Active Consoles", xalign=0), 0, row, 1, 1)
        self.ttys_combo = _combo()
        for value in TTY_COUNTS:
            self.ttys_combo.append_text(value)
        console_setup = os.path.join(self.fs_dir, "etc/default/console-setup")
        active = config.get_str(console_setup, "ACTIVE_CONSOLES=", "6")
        active = active.replace("/dev/tty[1-", "").replace("]", "")
        _select(self.ttys_combo, active)
        self.ttys_combo.connect("changed", self.on_ttys_changed)
        grid.attach(self.ttys_combo, 1, row, 1, 1)
        row += 1

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        grid.attach(close_btn, 0, row, 3, 1)

        self.window.show_all()

    def _populate_zones(self):
        zoneinfo = os.path.join(self.fs_dir, "usr/share/zoneinfo")
        zones = []
        for dirpath, dirnames, _files in os.walk(zoneinfo):
            for name in dirnames:
                rel = os.path.relpath(os.path.join(dirpath, name), zoneinfo)
                zones.append(rel)
        zones.sort()
        for zone in zones:
            self.zone_combo.append_text(zone)

        timezone_content = fsutil.load_file(os.path.join(self.fs_dir, "etc/timezone")).strip()
        parts = timezone_content.split("/")
        zone_value = parts[0] if parts else ""
        time_value = parts[1] if len(parts) > 1 else ""
        _select(self.zone_combo, zone_value)
        self._populate_times(zone_value)
        _select(self.time_combo, time_value)

    def _populate_times(self, zone):
        self.time_combo.remove_all()
        zone_dir = os.path.join(self.fs_dir, "usr/share/zoneinfo", zone)
        try:
            entries = sorted(f for f in os.listdir(zone_dir) if os.path.isfile(os.path.join(zone_dir, f)))
        except OSError:
            entries = []
        for entry in entries:
            self.time_combo.append_text(entry)

    def _populate_shells(self):
        available = [name for name in SHELL_CANDIDATES if os.path.exists(os.path.join(self.fs_dir, "bin", name))]
        for name in available:
            self.shell_combo.append_text(f"/bin/{name}")
        adduser_conf = os.path.join(self.fs_dir, "etc/adduser.conf")
        current = config.get_str(adduser_conf, "DSHELL=", "/bin/bash")
        _select(self.shell_combo, current)

    def on_apt_recommends_toggled(self, widget):
        apt_conf = os.path.join(self.fs_dir, "etc/apt/apt.conf")
        if widget.get_active():
            try:
                os.remove(apt_conf)
            except OSError:
                pass
        else:
            fsutil.save_file(apt_conf, 'APT::Install-Recommends "false";\nAPT::Install-Suggests "false";')

    def on_framebuffer_toggled(self, widget):
        casper_hook = os.path.join(self.fs_dir, "usr/share/initramfs-tools/conf-hooks.d/casper")
        config.replace_str(casper_hook, "FRAMEBUFFER=", "y" if widget.get_active() else "n")

    def on_zone_changed(self, widget):
        zone = widget.get_active_text()
        if zone is None:
            return
        self._populate_times(zone)

    def on_time_changed(self, widget):
        zone = self.zone_combo.get_active_text()
        time_value = widget.get_active_text()
        if not zone or not time_value:
            return
        fsutil.save_file(os.path.join(self.fs_dir, "etc/timezone"), f"{zone}/{time_value}")

    def on_shell_changed(self, widget):
        value = widget.get_active_text()
        if value is None:
            return
        config.replace_str(os.path.join(self.fs_dir, "etc/adduser.conf"), "DSHELL=", value)

    def on_ttys_changed(self, widget):
        value = widget.get_active_text()
        if value is None:
            return
        console_setup = os.path.join(self.fs_dir, "etc/default/console-setup")
        config.replace_str(console_setup, "ACTIVE_CONSOLES=", f"/dev/tty[1-{value}]")
