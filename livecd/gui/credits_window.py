"""Port of FCredits.class / FCredits.form."""

from .gtkcompat import Gtk

CREDITS_TEXT = """ubuntuDE Developer and Maintainer:
Kevin Atwood (a.k.a nooksee)
<kevin@nooksee.com>

Original Developer:
Ivailo Monev (a.k.a. SmiL3y)
<xakepa10@gmail.com>

PPA maintainer:
Michal Glowienka (a.k.a. eloaders)
<eloaders@yahoo.com>

Documentation:
Mubiin Kimura (a.k.a. clearkimura)
<clearkimura@gmail.com>

Gambas3 port:
Thiago Abreu (a.k.a thiagoabreu)
<thiagoa7@gmail.com>"""


class CreditsWindow:
    def __init__(self, parent):
        self.window = Gtk.Window(title="Credits")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_default_size(360, 340)
        self.window.set_border_width(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.get_buffer().set_text(CREDITS_TEXT)
        scroller.add(text_view)
        box.pack_start(scroller, True, True, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        box.pack_start(close_btn, False, False, 0)

        self.window.show_all()
