"""Port of FLicense.class / FLicense.form."""

from .gtkcompat import Gtk

LICENSE_TEXT = """LiveCD Creator 3
Copyright (C) 2012-2014  Kevin Atwood

Customizer
Copyright (C) 2010-2014  Ivailo Monev

This program is free software; you can redistribute it and/or modify it \
under the terms of the GNU General Public License as published by the Free \
Software Foundation; either version 2 of the License, or (at your option) \
any later version.

This program is distributed in the hope that it will be useful, but WITHOUT \
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or \
FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for \
more details.

You should have received a copy of the GNU General Public License along \
with this program; if not, write to the Free Software Foundation, Inc., \
51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA."""


class LicenseWindow:
    def __init__(self, parent):
        self.window = Gtk.Window(title="License")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_default_size(460, 380)
        self.window.set_border_width(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.get_buffer().set_text(LICENSE_TEXT)
        scroller.add(text_view)
        box.pack_start(scroller, True, True, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        box.pack_start(close_btn, False, False, 0)

        self.window.show_all()
