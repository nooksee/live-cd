# LiveUSB Creator
# Copyright (C) 2012-2014  Kevin Atwood
# Copyright (C) 2026  Kevin Thomas
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
Python backend). The current version is `0.1.0.dev0`, a pre-alpha recovery
baseline rather than a product release.

The project originally shipped as LiveCD Creator: a Gambas3 GUI backed by
Bash scripts. That implementation was retired from this repository on
2026-07-25 and archived, verified byte-identical, to
`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`; it remains visible
in git history before the rename. See [STATUS.md](STATUS.md) for the current
truth, [ROADMAP.md](ROADMAP.md) for the active recovery and modernization
route, and the
[dated Python-port handoff](docs/history/python-port.md)
for the original translation map and risk register.

A later installed-package reference, recovered from the 2016 ubuntuDE virtual
machine and validated against its Debian package metadata, is preserved under
[legacy/live-cd-3.13.93-installed/](legacy/live-cd-3.13.93-installed/).
It is a behavioral oracle, not active product source.

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
    mounts.py, chroot.py, transaction.py, extract.py, cdimage.py,
    chroot_shell.py, clean.py, deb.py, gui_install.py, hook.py,
    pkgm.py, preflight.py, preflight_runtime.py, factory_plan.py,
    qemu.py, rebuild.py, xnest.py
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
  `isohybrid` from `syslinux-utils`, `qemu-system-*`, `Xephyr`, `wget`,
  ImageMagick's `convert`

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
**not** picked up automatically — see the
[historical handoff](docs/history/python-port.md).

This GUI could not be visually tested in the environment it was written in
(no working GTK/X11 display) — sanity-check window layouts on a real desktop
before relying on it. See [STATUS.md](STATUS.md) for current acceptance state
and the [historical handoff](docs/history/python-port.md)
for the original verification record.

## Credits

Kevin Thomas 'nooksee' (project creator, product owner, current maintainer)

Current AI collaboration identities, roles, and lifecycle responsibilities
are recorded in [CONTRIBUTORS.md](CONTRIBUTORS.md).

Historical credits:

Kevin Atwood 'nooksee' (code developer) `admin@nooksee.com`

Ivailo Monev 'SmiL3y' (code developer) `xakepa10@gmail.com`

Michal Glowienka 'eloaders' (PPA maintainer) `eloaders@yahoo.com`

Mubiin Kimura 'clearkimura' (documentation) `clearkimura@gmail.com`

Thiago Abreu 'thiagoabreu' (Gambas 3 port) `thiagoa7@gmail.com`

Muhammad Bashir Al-Noimi 'mbnoimi' (64-bit tester for Gambas 3 port)

Ayman 'aymanim' (typo, spellcheck)


## Legal

The GNU General Public License version 2 or later (`GPL-2.0-or-later`).
See [LICENSE](LICENSE).

Copyright (C) 2026 Kevin Thomas

Copyright (C) 2010-2014 Kevin Atwood

Copyright (C) 2010-2013 Ivailo Monev

Copyright (C) 2013-2013 Mubiin Kimura
