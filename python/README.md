# LiveCD Creator 3 - Python port

This is a Python port of LiveCD Creator, originally written as a Gambas3
GTK application (`.src/`) backed by Bash scripts (`.hidden/scripts/`). It
reproduces the same functionality:

- a GTK3 (PyGObject) GUI equivalent to the Gambas forms in `.src/`
- a Python backend equivalent to `.hidden/common` and `.hidden/scripts/*`
- a `live-cd` command line tool equivalent to `.hidden/live-cd.sh`

## Layout

```
python/
  bin/
    live-cd          # CLI entrypoint (backend actions)
    live-cd-gui       # GUI entrypoint
  livecd/
    constants.py      # paths & defaults (was .hidden/default)
    messages.py        # colored console messages (was .hidden/common message helpers)
    config.py           # Get_Str/Replace_Str-style key=value file helpers (was Func module)
    fsutil.py            # editor/terminal/file-manager detection (was Func module)
    resources.py          # icon/pixmap/cli lookup, dev checkout + installed layouts
    cli.py                 # `live-cd` entrypoint (was live-cd.sh)
    backend/                # one module per original script in .hidden/scripts/
      mounts.py, chroot.py, extract.py, cdimage.py, chroot_shell.py,
      clean.py, deb.py, gui_install.py, hook.py, pkgm.py, qemu.py,
      rebuild.py, xnest.py
    gui/                     # one module per original .src/F*.class+form
      main_window.py (FMain), settings_window.py (FSettings),
      grub2_window.py (FGrub2), syslinux_window.py (FSysLinux),
      tweaks_window.py (FTweaks), packages_window.py (FPackages),
      downloader_window.py (FDownloader), about_window.py (FAbout),
      credits_window.py (FCredits), license_window.py (FLicense),
      checks.py (Check module), app.py (Check.Main / privilege elevation)
```

## Requirements

- Python 3.8+
- PyGObject + GTK3 (`gui` extra) for the graphical interface
- The same system tools the original relied on: `mount`, `chroot`,
  `unsquashfs`/`mksquashfs`, `rsync`, `genisoimage`, `qemu-system-*`,
  `Xephyr`, `wget`, ImageMagick's `convert`

The backend actions (`live-cd -e/-r/-c/...`) require root, exactly like the
original bash scripts.

## Usage

```
# Backend CLI (equivalent of live-cd.sh)
python/bin/live-cd --help
python/bin/live-cd -e     # extract an ISO into the work directory
python/bin/live-cd -r     # rebuild the ISO
python/bin/live-cd -c     # open a chroot shell

# GUI (equivalent of the Gambas app)
python/bin/live-cd-gui
```

Or install it properly:

```
pip install -e ./python[gui]
live-cd --help
live-cd-gui
```

## Notes on fidelity

This is a behavioral port, not a line-by-line transliteration:

- Config/state files (`/etc/live-cd/default`, `casper.conf`,
  `lsb-release`, `gfxboot.cfg`, etc.) use the same `KEY=value` line format
  and the same paths as the original, so an existing `/etc/live-cd`
  installation continues to work.
- GUI dialogs (file/directory choosers, warnings) use native GTK3 widgets
  rather than trying to pixel-match the original Gambas layouts.
- Original quirks were kept where they affect on-disk state (e.g. the
  `base_installable` check in `rebuild` inspects `$WORK_DIR/usr/bin/ubiquity`
  rather than `$WORK_DIR/FileSystem/usr/bin/ubiquity`, exactly like the
  original bash), so behavior stays identical for anyone relying on it.
  One purely cosmetic mismatch was not reproduced: the original GRUB
  colour picker's Select-Case handler used the British spelling
  ("dark-grey"/"light-grey") while its own combo box listed the American
  spelling ("dark-gray"/"light-gray", which is also what GRUB itself
  expects), so choosing those two entries never updated the live colour
  preview. The Python port uses one consistent spelling throughout, which
  only affects that preview, not the saved GRUB configuration.

This GUI could not be visually tested in the environment this port was
written in (no working GTK/X11 display), so please sanity-check window
layouts on a real desktop before relying on it.
