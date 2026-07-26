# LiveUSB Creator 3
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

The Customizer notice above records the GPL-licensed upstream lineage inherited
by LiveCD Creator and, through it, LiveUSB Creator. Customizer is not the
current project name or support destination. The current project home is
<https://github.com/nooksee/live-usb>.

An advanced LiveUSB customization and remastering tool. With it, you can build your own Ubuntu based remix using Ubuntu Mini Remix, Ubuntu or its derivatives from an ISO image.

This is the Python implementation of LiveUSB Creator (GTK3/PyGObject GUI +
Python backend), for Ubuntu 14.04 and its derivatives.

The project originally shipped as LiveCD Creator: a Gambas3 GUI backed by
Bash scripts. That implementation was retired from this repository on
2026-07-25 and archived, verified byte-identical, to
`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`; it remains visible
in git history before the rename. See `HANDOFF.md` for the architecture map,
risk register, and what has and has not been verified in this
implementation.

## Layout

```
bin/
  live-usb          # CLI entrypoint (backend actions)
  live-usb-gui       # GUI entrypoint
liveusb/
  constants.py      # paths & defaults
  messages.py        # colored console messages
  config.py           # Get_Str/Replace_Str-style key=value file helpers
  fsutil.py            # editor/terminal/file-manager detection
  resources.py          # icon/pixmap/cli lookup, dev checkout + installed layouts
  cli.py                 # `live-usb` entrypoint
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

The backend actions (`live-usb -e/-r/-c/...`) require root.

## Usage

```
# Backend CLI
bin/live-usb --help
bin/live-usb -e     # extract an ISO into the work directory
bin/live-usb -r     # rebuild the ISO
bin/live-usb -c     # open a chroot shell

# GUI
bin/live-usb-gui
```

Or install it properly:

```
pip install -e .[gui]
live-usb --help
live-usb-gui
```

## Notes on fidelity

Config/state files (`casper.conf`, `lsb-release`, `gfxboot.cfg`, etc.) use
the same `KEY=value` line format the retired Bash/Gambas implementation
used. The top-level config directory itself is a deliberate divergence,
not a fidelity claim: it is now `/etc/live-usb` (was `/etc/live-cd`), and
the default work directory is now `/home/live-usb` (was `/home/live-cd`).
An old `/etc/live-cd` installation from the original LiveCD Creator is
**not** picked up automatically — see `HANDOFF.md` §7.

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
