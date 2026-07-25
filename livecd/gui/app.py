"""GUI entrypoint, ported from Check.Main(): re-exec with a privilege helper
if not already root, then show the main window."""

import os
import shutil
import sys


def _relaunch_with(helper, script):
    os.execvp(helper, [helper, sys.executable, script])


def main():
    if os.geteuid() != 0:
        script = os.path.abspath(sys.argv[0])
        for helper in ("gksu", "kdesu", "pkexec"):
            if shutil.which(helper):
                _relaunch_with(helper, script)
                return 0
        print("No gksu, kdesu or pkexec available! Run this program as root.", file=sys.stderr)
        return 1

    from .gtkcompat import Gtk
    from .main_window import MainWindow

    MainWindow(on_close=Gtk.main_quit)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
