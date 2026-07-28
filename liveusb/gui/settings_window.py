"""Port of FSettings.class / FSettings.form: preferences window."""

from .gtkcompat import Gtk

from .. import config, constants

LOCALES = [
    "C", "POSIX", "bokmal", "catalan", "croatian", "czech", "danish", "dansk",
    "deutsch", "dutch", "eesti", "estonian", "finnish", "french", "galego",
    "galician", "german", "greek", "hebrew", "hrvatski", "hungarian",
    "icelandic", "italian", "japanese", "japanese.euc", "ja_JP", "ja_JP.ujis",
    "japanese.sjis", "korean", "korean.euc", "ko_KR", "lithuanian", "no_NO",
    "norwegian", "nynorsk", "polish", "portuguese", "romanian", "russian",
    "slovak", "slovene", "slovenian", "spanish", "swedish", "thai", "turkish",
]

RESOLUTIONS = [
    "1920x1200", "1920x1080", "1600x1200", "1600x900", "1280x1024",
    "1280x960", "1280x800", "1280x768", "1024x768", "800x600", "640x480",
]

VRAM_SIZES = ["64", "96", "128", "256", "384", "512", "768", "1024", "2048"]

COMPRESSION_FORMATS = ["gzip", "lzo", "xz"]


def _combo(values, readonly=True):
    combo = Gtk.ComboBoxText()
    for value in values:
        combo.append_text(value)
    return combo


def _select(combo, value, fallback_index=0):
    model = combo.get_model()
    for i, row in enumerate(model):
        if row[0] == value:
            combo.set_active(i)
            return
    combo.set_active(fallback_index)


def _get_vram_setting():
    return config.get_config_str("VRAM=", constants.DEFAULT_VRAM)


class SettingsWindow:
    def __init__(self, parent, on_close=None):
        self._on_close = on_close
        config.ensure_config_exists()

        self.window = Gtk.Window(title="Settings")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_resizable(False)
        self.window.set_border_width(8)
        self.window.connect("destroy", self._on_destroy)

        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        self.window.add(grid)
        row = 0

        grid.attach(Gtk.Label(label="Working Directory", xalign=0), 0, row, 2, 1)
        row += 1
        self.work_dir_entry = Gtk.Entry(editable=False)
        self.work_dir_entry.set_text(config.get_work_dir())
        self.change_wdir_btn = Gtk.Button(label="Change")
        self.change_wdir_btn.connect("clicked", self.on_change_work_dir)
        grid.attach(self.work_dir_entry, 0, row, 1, 1)
        grid.attach(self.change_wdir_btn, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="ISO Mount Directory", xalign=0), 0, row, 2, 1)
        row += 1
        self.mount_dir_entry = Gtk.Entry(editable=False)
        self.mount_dir_entry.set_text(config.get_mount_dir())
        change_mdir_btn = Gtk.Button(label="Change")
        change_mdir_btn.connect("clicked", self.on_change_mount_dir)
        grid.attach(self.mount_dir_entry, 0, row, 1, 1)
        grid.attach(change_mdir_btn, 1, row, 1, 1)
        row += 1

        self.use_colors_check = Gtk.CheckButton(label="Use Color in Messages")
        self.use_colors_check.set_active(config.get_config_str("MESSAGES_COLORS=", "1") != "0")
        self.use_colors_check.connect("toggled", self.on_use_colors_toggled)
        grid.attach(self.use_colors_check, 0, row, 2, 1)
        row += 1

        self.force_chroot_check = Gtk.CheckButton(label="Skip FileSystem Lock Check")
        self.force_chroot_check.set_active(config.get_config_str("FORCE_CHROOT=", "0") == "1")
        self.force_chroot_check.connect("toggled", self.on_force_chroot_toggled)
        grid.attach(self.force_chroot_check, 0, row, 2, 1)
        row += 1

        self.apt_helper_check = Gtk.CheckButton(label="Use apt-helper")
        self.apt_helper_check.set_active(config.get_config_str("APT_HELPER=", "1") != "0")
        self.apt_helper_check.connect("toggled", self.on_apt_helper_toggled)
        grid.attach(self.apt_helper_check, 0, row, 2, 1)
        row += 1

        self.boot_files_check = Gtk.CheckButton(label="Delete Boot Files")
        self.boot_files_check.set_active(config.get_config_str("BOOT_FILES=", "0") == "1")
        self.boot_files_check.connect("toggled", self.on_boot_files_toggled)
        grid.attach(self.boot_files_check, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label="Locale", xalign=0), 0, row, 1, 1)
        self.locales_combo = _combo(LOCALES)
        _select(self.locales_combo, config.get_config_str("LOCALES=", ""))
        self.locales_combo.connect("changed", self.on_locales_changed)
        grid.attach(self.locales_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Virtual Desktop Resolution", xalign=0), 0, row, 1, 1)
        self.resolution_combo = _combo(RESOLUTIONS)
        _select(self.resolution_combo, config.get_config_str("RESOLUTION=", "800x600"))
        self.resolution_combo.connect("changed", self.on_resolution_changed)
        grid.attach(self.resolution_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Desktop Emulator Memory", xalign=0), 0, row, 1, 1)
        self.vram_combo = _combo(VRAM_SIZES)
        _select(self.vram_combo, _get_vram_setting())
        self.vram_combo.connect("changed", self.on_vram_changed)
        grid.attach(self.vram_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Compression Format", xalign=0), 0, row, 1, 1)
        self.compression_combo = _combo(COMPRESSION_FORMATS)
        _select(self.compression_combo, config.get_config_str("COMPRESSION=", "tar"))
        self.compression_combo.connect("changed", self.on_compression_changed)
        grid.attach(self.compression_combo, 1, row, 1, 1)
        row += 1

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        grid.attach(close_btn, 0, row, 2, 1)

        self.window.show_all()

    def set_work_dir_locked(self, locked):
        self.change_wdir_btn.set_sensitive(not locked)
        if locked:
            self.change_wdir_btn.set_tooltip_text("Changing the working directory is not possible!")

    def on_use_colors_toggled(self, widget):
        config.replace_config_str("MESSAGES_COLORS=", "1" if widget.get_active() else "0")

    def on_force_chroot_toggled(self, widget):
        config.replace_config_str("FORCE_CHROOT=", "1" if widget.get_active() else "0")

    def on_apt_helper_toggled(self, widget):
        config.replace_config_str("APT_HELPER=", "1" if widget.get_active() else "0")

    def on_boot_files_toggled(self, widget):
        config.replace_config_str("BOOT_FILES=", "1" if widget.get_active() else "0")

    def on_resolution_changed(self, widget):
        config.replace_config_str("RESOLUTION=", widget.get_active_text())

    def on_vram_changed(self, widget):
        config.replace_config_str("VRAM=", widget.get_active_text())

    def on_compression_changed(self, widget):
        config.replace_config_str("COMPRESSION=", widget.get_active_text())

    def on_locales_changed(self, widget):
        config.replace_config_str("LOCALES=", widget.get_active_text())

    def on_change_work_dir(self, _widget):
        path = self._pick_directory("Please, select work directory", config.get_work_dir())
        if path is None:
            return
        config.replace_config_str("WORK_DIR=", path)
        self.work_dir_entry.set_text(path)

    def on_change_mount_dir(self, _widget):
        path = self._pick_directory("Please, select work directory", config.get_mount_dir())
        if path is None:
            return
        config.replace_config_str("MOUNT_DIR=", path)
        self.mount_dir_entry.set_text(path)

    def _pick_directory(self, title, initial):
        dialog = Gtk.FileChooserDialog(
            title=title, transient_for=self.window, action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        import os
        if initial and os.path.exists(initial):
            dialog.set_filename(initial)
        else:
            dialog.set_current_folder("/home")
        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return path

    def _on_destroy(self, _widget):
        if self._on_close:
            self._on_close()
