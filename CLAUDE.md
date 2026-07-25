# LiveCD Creator — project notes

An Ubuntu LiveCD remastering tool, implemented in Python (GTK3/PyGObject GUI
+ Python backend).

The project originally shipped as a Gambas3 GUI backed by Bash scripts. That
implementation (`.src/`, `.hidden/`) was retired from this repository on
2026-07-25 at Kevin's explicit request and archived, verified byte-identical
(content, checksums, and permissions including executable bits), to
`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`. Full git history is
intact — it is still visible with `git show` against any commit before
`edfcc62`. There is no longer a second implementation to keep in sync
against; this repository is the sole reference.

Read `HANDOFF.md` before working on this code. It has the architecture map
(including the mapping from the retired original's scripts/forms to their
Python counterparts), the risk register, and a concrete first-session plan.
**The port has never been run against a real ISO** — treat the first real
remastering run as the actual acceptance test.

## Hard constraints

1. **Config format compatibility is non-negotiable.** This tool reads and
   writes `/etc/live-cd/default`, `/etc/live-cd/exclude`, and inside the
   target filesystem `etc/casper.conf`, `etc/lsb-release`,
   `ISO/isolinux/gfxboot.cfg`, `usr/share/desktop-base/grub_background.sh`,
   all in the same `KEY=value` line format the original Bash/Gambas tool
   used. An existing `/etc/live-cd` installation or work directory from the
   original tool must continue to work unchanged.
2. **Deliberate divergences get documented.** If you fix a bug or change
   behaviour relative to what the original Bash/Gambas implementation did
   (see `HANDOFF.md` §7, "Deliberate divergences from the original"), record
   it there. The original is gone from the repo, but its behaviour is still
   the compatibility baseline for on-disk state.

## Environment facts

- Backend actions need **root** — they chroot, loop-mount, and unmount.
- Real end-to-end testing needs an Ubuntu ISO plus `unsquashfs`, `mksquashfs`,
  `rsync`, `genisoimage`, `qemu-system-x86_64`, `Xephyr`.
- The GUI needs PyGObject + GTK3 (`gir1.2-gtk-3.0`). It can be smoke-tested
  headlessly under Xvfb — recipe in `HANDOFF.md`.
- Targets Python 3.8+, stdlib only apart from PyGObject for the GUI.

## Commands

```bash
# Compile check (fast, catches most edits)
python3 -m py_compile livecd/*.py livecd/backend/*.py livecd/gui/*.py

# CLI
bin/live-cd --help
sudo bin/live-cd -e     # extract   -r rebuild   -c chroot   -t clean

# GUI
bin/live-cd-gui
```

There is no test suite yet. Adding one is the single highest-value
contribution — see `HANDOFF.md`.
