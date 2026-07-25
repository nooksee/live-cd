"""Port of FSysLinux.class / FSysLinux.form: isolinux splash/colour customization."""

import os
import subprocess

from .gtkcompat import Gtk, GdkPixbuf, Gdk

from .. import config, fsutil, messages

COLOR_MODES = ["Selected", "Normal", "Screen-Colour"]


def _rgba_from_hex(hex_str):
    rgba = Gdk.RGBA()
    rgba.parse(f"#{hex_str.zfill(6)}")
    return rgba


def _hex_from_rgba(rgba):
    return "{:02x}{:02x}{:02x}".format(
        round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
    )


class SysLinuxWindow:
    def __init__(self, parent):
        config.ensure_config_exists()
        self.work_dir = config.get_work_dir()
        self.isolinux_dir = os.path.join(self.work_dir, "ISO/isolinux")
        self.gfxboot_cfg = os.path.join(self.isolinux_dir, "gfxboot.cfg")

        splash_pcx = os.path.join(self.isolinux_dir, "splash.pcx")
        splash_jpg = os.path.join(self.isolinux_dir, "splash.jpg")
        has_splash = os.path.exists(splash_pcx) or os.path.exists(splash_jpg)
        has_gfxboot = os.path.exists(self.gfxboot_cfg)

        if not has_splash and not has_gfxboot:
            self._error_and_close(parent, "Nothing to do here")
            return

        self.window = Gtk.Window(title="SysLinux")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_resizable(False)
        self.window.set_border_width(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        self.splash_image = Gtk.Image()
        box.pack_start(self.splash_image, True, True, 0)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.normal_label = Gtk.Label(label="Normal")
        self.selected_label = Gtk.Label(label="Selected")
        preview_box.pack_start(self.normal_label, True, True, 0)
        preview_box.pack_start(self.selected_label, True, True, 0)
        box.pack_start(preview_box, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.change_pic_btn = Gtk.Button(label="Change Splash")
        self.change_pic_btn.connect("clicked", self.on_change_pic)
        controls.pack_start(self.change_pic_btn, False, False, 0)

        if not has_splash:
            messages.warning("Splash file does not exists!")
            self.change_pic_btn.set_sensitive(False)
        else:
            self._load_splash_image(splash_pcx if os.path.exists(splash_pcx) else splash_jpg)

        if has_gfxboot:
            self.mode_combo = Gtk.ComboBoxText()
            for mode in COLOR_MODES:
                self.mode_combo.append_text(mode)
            self.mode_combo.set_active(0)
            self.mode_combo.connect("changed", self.on_mode_changed)
            self.color_button = Gtk.ColorButton()
            self.color_button.connect("color-set", self.on_color_set)
            controls.pack_start(self.mode_combo, False, False, 0)
            controls.pack_start(self.color_button, False, False, 0)
            self._load_colors()
        else:
            self.mode_combo = None
            self.color_button = None

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        controls.pack_start(close_btn, False, False, 0)
        box.pack_start(controls, False, False, 0)

        self.window.show_all()

    def _error_and_close(self, parent, text):
        dialog = Gtk.MessageDialog(
            transient_for=parent, flags=0, message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK, text=text,
        )
        dialog.run()
        dialog.destroy()
        self.window = None

    def _load_splash_image(self, path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 360, 270, True)
            self.splash_image.set_from_pixbuf(pixbuf)
        except Exception:
            pass

    def _load_colors(self):
        fg = config.get_str(self.gfxboot_cfg, "foreground=0x", "000000")
        bg = config.get_str(self.gfxboot_cfg, "background=0x", "000000")
        screen = config.get_str(self.gfxboot_cfg, "screen-colour=0x", "000000")
        self._fg, self._bg, self._screen = fg, bg, screen

        self.selected_label.override_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(fg))
        self.selected_label.override_background_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(screen))
        self.normal_label.override_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(bg))

        self.color_button.set_tooltip_text(f"Change the colour used for {self.mode_combo.get_active_text()}")
        self._sync_color_button()

    def _sync_color_button(self):
        mode = self.mode_combo.get_active_text()
        current = {"Selected": self._fg, "Normal": self._bg, "Screen-Colour": self._screen}[mode]
        self.color_button.set_rgba(_rgba_from_hex(current))

    def on_mode_changed(self, widget):
        self.color_button.set_tooltip_text(f"Change the colour used for {widget.get_active_text()}")
        self._sync_color_button()

    def on_color_set(self, widget):
        mode = self.mode_combo.get_active_text()
        hex_value = _hex_from_rgba(widget.get_rgba())

        if mode == "Selected":
            messages.event_msg(f"Changing syslinux foreground colour: {hex_value}")
            self._fg = hex_value
            self.selected_label.override_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(hex_value))
            config.replace_str_as_is(self.gfxboot_cfg, "foreground=0x", hex_value)
        elif mode == "Normal":
            messages.event_msg(f"Changing syslinux background colour: {hex_value}")
            self._bg = hex_value
            self.normal_label.override_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(hex_value))
            config.replace_str_as_is(self.gfxboot_cfg, "background=0x", hex_value)
        else:
            messages.debug_msg(f"Changing syslinux screen-colour colour: {hex_value}")
            self._screen = hex_value
            self.selected_label.override_background_color(Gtk.StateFlags.NORMAL, _rgba_from_hex(hex_value))
            config.replace_str_as_is(self.gfxboot_cfg, "screen-colour=0x", hex_value)

    def on_change_pic(self, _widget):
        pic = config.get_config_str("PIC=", "")
        dialog = Gtk.FileChooserDialog(
            title="Please select picture", transient_for=self.window, action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if pic and os.path.exists(pic):
            dialog.set_filename(pic)
        else:
            dialog.set_current_folder("/home")
        filt = Gtk.FileFilter()
        filt.set_name("Pictures")
        for pattern in ("*.pcx", "*.png", "*.bmp", "*.jpeg", "*.jpg"):
            filt.add_pattern(pattern)
        dialog.add_filter(filt)
        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path is None:
            return

        config.replace_config_str("PIC=", path)
        splash_pcx = os.path.join(self.isolinux_dir, "splash.pcx")
        target = splash_pcx if os.path.exists(splash_pcx) else os.path.join(self.isolinux_dir, "splash.jpg")

        messages.event_msg(f"Replacing picture: {target}")
        result = subprocess.run(["convert", "-resize", "640x480!", "-colors", "256", path, target])
        if result.returncode != 0:
            messages.warning(f"Unable to convert {path} to {target}")
            return
        self._load_splash_image(target)
