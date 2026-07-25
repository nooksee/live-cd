"""Port of FAbout.class / FAbout.form."""

from .gtkcompat import Gtk, GdkPixbuf

from .. import __version__, resources


class AboutWindow:
    def __init__(self, parent):
        self.window = Gtk.Window(title="About")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_resizable(False)
        self.window.set_border_width(10)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        title = Gtk.Label()
        title.set_markup(f"<span size='xx-large' weight='bold'>LiveUSB Creator 3</span>")
        box.pack_start(title, False, False, 0)

        icon_path = resources.app_icon_path()
        if icon_path:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 64, 64)
                box.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
            except Exception:
                pass

        label = Gtk.Label(label="An advanced LiveUSB customization and remastering tool.")
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        box.pack_start(label, False, False, 0)

        version_label = Gtk.Label(label=f"Python port {__version__}")
        box.pack_start(version_label, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        box.pack_start(close_btn, False, False, 0)

        self.window.show_all()
