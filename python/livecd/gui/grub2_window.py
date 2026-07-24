"""Port of FGrub2.class / FGrub2.form: GRUB2 splash/colour customization."""

import os
import subprocess

from .gtkcompat import Gtk, GdkPixbuf

from .. import config, fsutil, messages

GRUB_COLORS = [
    "black", "blue", "white", "brown", "cyan", "yellow", "magenta", "red",
    "green", "dark-gray", "light-cyan", "light-blue", "light-green",
    "light-gray", "light-magenta", "light-red",
]

COLOR_HEX = {
    "black": "#000000",
    "blue": "#0000AA",
    "white": "#FFFFFF",
    "brown": "#D2691E",
    "cyan": "#00AAAA",
    "yellow": "#AAAA00",
    "magenta": "#AA00AA",
    "red": "#AA0000",
    "green": "#00AA00",
    "dark-gray": "#555555",
    "light-cyan": "#55FFFF",
    "light-blue": "#5555FF",
    "light-green": "#55FF55",
    "light-gray": "#AAAAAA",
    "light-magenta": "#FF55FF",
    "light-red": "#FF5555",
}


def _rgba(name):
    from .gtkcompat import Gdk
    rgba = Gdk.RGBA()
    rgba.parse(COLOR_HEX.get(name, "#000000"))
    return rgba


class Grub2Window:
    def __init__(self, parent):
        config.ensure_config_exists()
        self.work_dir = config.get_work_dir()
        self.fs_dir = os.path.join(self.work_dir, "FileSystem")
        self.grub_bg_script = os.path.join(self.fs_dir, "usr/share/desktop-base/grub_background.sh")

        desktop_base = os.path.join(self.fs_dir, "usr/share/desktop-base")
        if not os.path.isdir(desktop_base):
            self._warn_and_close(parent, "Install desktop-base package and retry!")
            return
        if not os.path.exists(self.grub_bg_script):
            try:
                fsutil.save_file(self.grub_bg_script, "")
            except OSError:
                pass

        self.window = Gtk.Window(title="Grub2")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_resizable(False)
        self.window.set_border_width(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        self.splash_image = Gtk.Image()
        box.pack_start(self.splash_image, True, True, 0)

        self.normal_label = Gtk.Label(label="Normal")
        self.selected_label = Gtk.Label(label="Selected")
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_box.pack_start(self.normal_label, True, True, 0)
        preview_box.pack_start(self.selected_label, True, True, 0)
        box.pack_start(preview_box, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.append_text("NORMAL")
        self.mode_combo.append_text("HIGHLIGHT")
        self.color1_combo = Gtk.ComboBoxText()
        self.color2_combo = Gtk.ComboBoxText()
        for combo in (self.color1_combo, self.color2_combo):
            for name in GRUB_COLORS:
                combo.append_text(name)
        change_pic_btn = Gtk.Button(label="Change Splash")
        change_pic_btn.connect("clicked", self.on_change_pic)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())

        controls.pack_start(self.mode_combo, False, False, 0)
        controls.pack_start(self.color1_combo, False, False, 0)
        controls.pack_start(self.color2_combo, False, False, 0)
        controls.pack_start(change_pic_btn, False, False, 0)
        controls.pack_start(close_btn, False, False, 0)
        box.pack_start(controls, False, False, 0)

        self._load_splash_image()

        normal = config.get_str(self.grub_bg_script, "COLOR_NORMAL=", "light-blue/black").split("/")
        self._set_combo(self.color1_combo, normal[0] if normal else "light-blue")
        self._set_combo(self.color2_combo, normal[1] if len(normal) > 1 else "black")
        self.mode_combo.set_active(0)

        self._apply_preview()

        self.mode_combo.connect("changed", self.on_mode_changed)
        self.color1_combo.connect("changed", self.on_color_changed)
        self.color2_combo.connect("changed", self.on_color_changed)

        self.window.show_all()

    def _warn_and_close(self, parent, text):
        dialog = Gtk.MessageDialog(
            transient_for=parent, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK, text=text,
        )
        dialog.run()
        dialog.destroy()
        self.window = None

    def _set_combo(self, combo, value):
        model = combo.get_model()
        for i, row in enumerate(model):
            if row[0] == value:
                combo.set_active(i)
                return
        combo.set_active(0)

    def _load_splash_image(self):
        rel = config.get_str(self.grub_bg_script, "WALLPAPER=", "/boot/grub/splash.png")
        path = os.path.join(self.fs_dir, rel.lstrip("/"))
        if os.path.exists(path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 400, 300, True)
                self.splash_image.set_from_pixbuf(pixbuf)
            except Exception:
                pass

    def _apply_preview(self):
        fg = self.color1_combo.get_active_text()
        bg = self.color2_combo.get_active_text()
        if fg is None or bg is None:
            return
        target = self.normal_label if self.mode_combo.get_active_text() == "NORMAL" else self.selected_label
        target.override_color(Gtk.StateFlags.NORMAL, _rgba(fg))
        target.override_background_color(Gtk.StateFlags.NORMAL, _rgba(bg))

    def on_mode_changed(self, _widget):
        mode = self.mode_combo.get_active_text()
        colors = config.get_str(self.grub_bg_script, f"COLOR_{mode}=", "light-blue/black").split("/")
        self._set_combo(self.color1_combo, colors[0] if colors else "light-blue")
        self._set_combo(self.color2_combo, colors[1] if len(colors) > 1 else "black")

    def on_color_changed(self, _widget):
        mode = self.mode_combo.get_active_text()
        fg = self.color1_combo.get_active_text()
        bg = self.color2_combo.get_active_text()
        if fg is None or bg is None:
            return
        config.replace_str(self.grub_bg_script, f"COLOR_{mode}=", f"{fg}/{bg}")
        self._apply_preview()

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
        for pattern in ("*.png", "*.bmp", "*.jpeg", "*.jpg"):
            filt.add_pattern(pattern)
        dialog.add_filter(filt)
        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if path is None:
            return

        config.replace_config_str("PIC=", path)
        target = os.path.join(self.fs_dir, "boot/grub/splash.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        result = subprocess.run(["convert", "-resize", "800x600!", "-channel", "rgb", path, target])
        if result.returncode != 0:
            messages.warning(f"Unable to convert {path} to {target}")
            return
        config.replace_str(self.grub_bg_script, "WALLPAPER=", "/boot/grub/splash.png")
        self._load_splash_image()
