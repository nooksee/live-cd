"""Port of FDownloader.class / FDownloader.form: Ubuntu ISO downloader."""

import os
import re
import subprocess
import threading

from .gtkcompat import Gtk, GLib

RELEASES = {
    "Ubuntu Base Install (i386)": (
        "http://www.ubuntu-mini-remix.org/download/12.04/ubuntu-mini-remix-12.04-i386.iso",
        "ubuntu-mini-remix-12.04-i386.iso",
    ),
    "Ubuntu Base Install (amd64)": (
        "http://www.ubuntu-mini-remix.org/download/12.04/ubuntu-mini-remix-12.04-amd64.iso",
        "ubuntu-mini-remix-12.04-amd64.iso",
    ),
    "Ubuntu Full Install (i386)": (
        "http://releases.ubuntu.com/14.04/ubuntu-14.04.2-desktop-i386.iso",
        "ubuntu-14.04.2-desktop-i386.iso",
    ),
    "Ubuntu Full Install (amd64)": (
        "http://releases.ubuntu.com/14.04/ubuntu-14.04.2-desktop-amd64.iso",
        "ubuntu-14.04.2-desktop-amd64.iso",
    ),
}

_PERCENT_RE = re.compile(r"(\d{1,3})%")


class DownloaderWindow:
    def __init__(self, on_close=None):
        self._on_close = on_close
        self._proc = None

        self.window = Gtk.Window(title="Download Ubuntu")
        self.window.set_resizable(False)
        self.window.set_border_width(8)
        self.window.connect("destroy", self._on_destroy)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        box.pack_start(Gtk.Label(label="Select an Ubuntu release", xalign=0), False, False, 0)

        self.release_combo = Gtk.ComboBoxText()
        for name in RELEASES:
            self.release_combo.append_text(name)
        self.release_combo.set_active(0)
        box.pack_start(self.release_combo, False, False, 0)

        box.pack_start(
            Gtk.Label(label='Select where the ISO image will be saved and press "Download"', xalign=0),
            False, False, 0,
        )
        self.dir_chooser = Gtk.FileChooserButton(title="Select destination", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.dir_chooser.set_current_folder("/home")
        box.pack_start(self.dir_chooser, False, False, 0)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_no_show_all(True)
        box.pack_start(self.progress_bar, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.download_btn = Gtk.Button(label="Download")
        self.download_btn.connect("clicked", self.on_download)
        self.cancel_btn = Gtk.Button(label="Stop")
        self.cancel_btn.set_no_show_all(True)
        self.cancel_btn.connect("clicked", self.on_cancel)
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda _w: self.window.destroy())
        button_box.pack_start(self.download_btn, False, False, 0)
        button_box.pack_start(self.cancel_btn, False, False, 0)
        button_box.pack_start(quit_btn, False, False, 0)
        box.pack_start(button_box, False, False, 0)

        self.window.show_all()
        self.progress_bar.hide()
        self.cancel_btn.hide()

    def on_download(self, _widget):
        release = self.release_combo.get_active_text()
        if release not in RELEASES:
            return
        url, filename = RELEASES[release]
        destination = os.path.join(self.dir_chooser.get_filename() or "/home", filename)

        self.progress_bar.set_fraction(0)
        self.progress_bar.show()
        self.cancel_btn.show()

        self._proc = subprocess.Popen(
            ["wget", "-O", destination, url], stderr=subprocess.PIPE, universal_newlines=True,
        )
        thread = threading.Thread(target=self._watch_download, args=(self._proc,), daemon=True)
        thread.start()

    def _watch_download(self, proc):
        for line in proc.stderr:
            match = _PERCENT_RE.search(line)
            if match:
                percent = int(match.group(1))
                GLib.idle_add(self.progress_bar.set_fraction, percent / 100)
        proc.wait()
        GLib.idle_add(self._on_download_finished, proc.returncode)

    def _on_download_finished(self, returncode):
        self.cancel_btn.hide()
        if returncode == 0:
            self.progress_bar.set_fraction(1)
            dialog = Gtk.MessageDialog(
                transient_for=self.window, flags=0, message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK, text="Download completed!",
            )
            dialog.run()
            dialog.destroy()
        self._proc = None

    def on_cancel(self, _widget):
        if self._proc is not None:
            self._proc.terminate()
        self.progress_bar.hide()
        self.cancel_btn.hide()

    def _on_destroy(self, _widget):
        if self._proc is not None:
            self._proc.terminate()
        if self._on_close:
            self._on_close()
