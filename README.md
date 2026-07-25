# LiveCD Creator 3
# Copyright (C) 2012-2014  Kevin Atwood
# 
# Customizer - Advanced LiveCD Remastering Tool
# Copyright (C) 2010-2013  Ivailo Monev
# 
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

An advanced LiveCD customization and remastering tool. With it, you can build your own Ubuntu based remix using Ubuntu Mini Remix, Ubuntu or its derivatives from an ISO image.

This is the Python implementation of LiveCD Creator (GTK3/PyGObject GUI +
Python backend), for Ubuntu 14.04 and its derivatives.

The project originally shipped as a Gambas3 GUI backed by Bash scripts. That
implementation was retired from this repository on 2026-07-25 and archived,
verified byte-identical, to
`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`; it remains visible
in git history before commit `edfcc62`. See `HANDOFF.md` for the
architecture map, risk register, and what has and has not been verified in
this implementation.

## Layout

```
bin/
  live-cd          # CLI entrypoint (backend actions)
  live-cd-gui       # GUI entrypoint
livecd/
  constants.py      # paths & defaults
  messages.py        # colored console messages
  config.py           # Get_Str/Replace_Str-style key=value file helpers
  fsutil.py            # editor/terminal/file-manager detection
  resources.py          # icon/pixmap/cli lookup, dev checkout + installed layouts
  cli.py                 # `live-cd` entrypoint
  backend/                # one module per backend action
    mounts.py, chroot.py, extract.py, cdimage.py, chroot_shell.py,
    clean.py, deb.py, gui_install.py, hook.py, pkgm.py, qemu.py,
    rebuild.py, xnest.py
  gui/                     # one module per GUI window
    main_window.py, settings_window.py, grub2_window.py,
    syslinux_window.py, tweaks_window.py, packages_window.py,
    downloader_window.py, about_window.py, credits_window.py,
    license_window.py, checks.py, app.py
```

## Requirements

- Python 3.8+
- PyGObject + GTK3 (`gui` extra) for the graphical interface
- `mount`, `chroot`, `unsquashfs`/`mksquashfs`, `rsync`, `genisoimage`,
  `qemu-system-*`, `Xephyr`, `wget`, ImageMagick's `convert`

The backend actions (`live-cd -e/-r/-c/...`) require root.

## Usage

```
# Backend CLI
bin/live-cd --help
bin/live-cd -e     # extract an ISO into the work directory
bin/live-cd -r     # rebuild the ISO
bin/live-cd -c     # open a chroot shell

# GUI
bin/live-cd-gui
```

Or install it properly:

```
pip install -e .[gui]
live-cd --help
live-cd-gui
```

## Notes on fidelity

Config/state files (`/etc/live-cd/default`, `casper.conf`, `lsb-release`,
`gfxboot.cfg`, etc.) use the same `KEY=value` line format and the same paths
as the retired Bash/Gambas implementation, so an existing `/etc/live-cd`
installation continues to work unchanged.

This GUI could not be visually tested in the environment it was written in
(no working GTK/X11 display) — sanity-check window layouts on a real desktop
before relying on it. See `HANDOFF.md` for the full list of what has and has
not been verified.

## Credits

Kevin Atwood 'nooksee' (code developer) `admin@nooksee.com`

Ivailo Monev 'SmiL3y' (code developer) `xakepa10@gmail.com`

Michal Glowienka 'eloaders' (PPA maintainer) `eloaders@yahoo.com`

Mubiin Kimura 'clearkimura' (documentation) `clearkimura@gmail.com`

Thiago Abreu 'thiagoabreu' (Gambas 3 port) `thiagoa7@gmail.com`

Muhammad Bashir Al-Noimi 'mbnoimi' (64-bit tester for Gambas 3 port)

Ayman 'aymanim' (typo, spellcheck)


## Legal

The GNU General Public License version 2 (GPLv2)

Copyright (C) 2010-2014 Kevin Atwood

Copyright (C) 2010-2013 Ivailo Monev

Copyright (C) 2013-2013 Mubiin Kimura