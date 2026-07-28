"""Central place to import GTK3 bindings so every window module agrees on the version."""

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402,F401
