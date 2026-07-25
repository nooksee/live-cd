# LiveCD Creator — project notes

An Ubuntu LiveCD remastering tool. The repo now contains **two implementations
of the same program**:

| | Location | Status |
| --- | --- | --- |
| Original | `.src/` (Gambas3 GUI), `.hidden/` (Bash backend) | Reference implementation. Working, shipped. |
| Python port | `python/` | Complete, structurally tested, **never run against a real ISO**. |

Read `python/HANDOFF.md` before working on the port. It has the architecture
map, the risk register, and a concrete first-session plan.

## Hard constraints

1. **The original is the reference.** When the port and the Bash/Gambas
   original disagree about behaviour, the original wins unless there's a
   documented reason. Don't modify `.src/` or `.hidden/` unless explicitly
   asked — they're the spec.
2. **Config format compatibility is non-negotiable.** Both implementations
   read and write the same files in the same `KEY=value` line format:
   `/etc/live-cd/default`, `/etc/live-cd/exclude`, and inside the target
   filesystem `etc/casper.conf`, `etc/lsb-release`, `ISO/isolinux/gfxboot.cfg`,
   `usr/share/desktop-base/grub_background.sh`. A user must be able to switch
   between the Bash and Python versions mid-project against the same work
   directory.
3. **Deliberate divergences get documented.** If you fix a bug that exists in
   the original, record it in `python/HANDOFF.md` under "Deliberate
   divergences". Silent behaviour changes are how the two implementations
   drift apart.

## Environment facts

- Backend actions need **root** — they chroot, loop-mount, and unmount.
- Real end-to-end testing needs an Ubuntu ISO plus `unsquashfs`, `mksquashfs`,
  `rsync`, `genisoimage`, `qemu-system-x86_64`, `Xephyr`.
- The GUI needs PyGObject + GTK3 (`gir1.2-gtk-3.0`). It can be smoke-tested
  headlessly under Xvfb — recipe in `python/HANDOFF.md`.
- Python port targets 3.8+, stdlib only apart from PyGObject for the GUI.

## Commands

```bash
# Compile check (fast, catches most edits)
cd python && python3 -m py_compile livecd/*.py livecd/backend/*.py livecd/gui/*.py

# CLI
python/bin/live-cd --help
sudo python/bin/live-cd -e     # extract   -r rebuild   -c chroot   -t clean

# GUI
python/bin/live-cd-gui
```

There is no test suite yet. Adding one is the single highest-value
contribution — see `python/HANDOFF.md`.
