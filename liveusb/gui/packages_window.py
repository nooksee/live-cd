"""Port of FPackages.class / FPackages.form: installed package list viewer."""

import os
import shutil
import subprocess

from .gtkcompat import Gtk

from .. import config, messages

PACKAGES_LIST_FILE = "/tmp/PACKAGESLIST"


class PackagesWindow:
    def __init__(self, parent):
        config.ensure_config_exists()
        self.work_dir = config.get_work_dir()

        self.window = Gtk.Window(title="Installed Packages")
        self.window.set_transient_for(parent)
        self.window.set_modal(True)
        self.window.set_default_size(640, 460)
        self.window.set_border_width(8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window.add(box)

        messages.event_msg("Getting package list")
        rows = self._load_packages()

        self.store = Gtk.ListStore(str, str, str, str)
        for row in rows:
            self.store.append(row)

        self.tree_view = Gtk.TreeView(model=self.store)
        for index, title in enumerate(["Package", "Version", "Priority", "Installed-Size"]):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_sort_column_id(index)
            self.tree_view.append_column(column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.tree_view)
        box.pack_start(scroller, True, True, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_btn = Gtk.Button(label="Save")
        save_btn.connect("clicked", self.on_save)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _w: self.window.destroy())
        button_box.pack_start(save_btn, False, False, 0)
        button_box.pack_start(close_btn, False, False, 0)
        box.pack_start(button_box, False, False, 0)

        self.window.show_all()

    def _load_packages(self):
        fs_dir = os.path.join(self.work_dir, "FileSystem")
        result = subprocess.run(
            ["chroot", fs_dir, "dpkg-query", "-W",
             "--showformat=${Package},${Version},${Priority},${Installed-Size}\n"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        with open(PACKAGES_LIST_FILE, "w") as fh:
            fh.write(result.stdout)

        rows = []
        for line in result.stdout.splitlines():
            fields = line.split(",")
            fields += [""] * (4 - len(fields))
            rows.append(fields[:4])
        return rows

    def on_save(self, _widget):
        dialog = Gtk.FileChooserDialog(
            title="Select where to save the list", transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_SAVE, Gtk.ResponseType.OK)
        dialog.set_current_folder(self.work_dir)
        dialog.set_current_name("packages.txt")
        response = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not path:
            return
        if not path.endswith(".txt"):
            path += ".txt"
        try:
            shutil.copyfile(PACKAGES_LIST_FILE, path)
        except OSError:
            error_dialog = Gtk.MessageDialog(
                transient_for=self.window, flags=0, message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Unable to copy {PACKAGES_LIST_FILE} to {path}",
            )
            error_dialog.run()
            error_dialog.destroy()
