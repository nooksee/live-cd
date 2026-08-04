# LiveUSB Creator — project notes

An Ubuntu LiveUSB remastering tool, implemented in Python (GTK3/PyGObject GUI
+ Python backend).

## Project stewardship

Read `CONTRIBUTORS.md` for current ownership, collaboration identities,
lifecycle roles, attribution practice, and license continuity.

- Kevin Thomas is the project creator, product owner, current maintainer, and
  final acceptance authority.
- George Prime is the OpenAI Codex AI project steward and orchestration lead.
- Claude Devens is the project-facing Anthropic Claude AI architecture and
  review collaborator.
- Jacob Codex is the OpenAI Codex AI implementation and verification
  collaborator, continuing the transposed OSAI III implementation role.

One active writer owns each assigned surface. Claude Devens should prioritize
behavioral recovery, architectural challenge, adversarial review, failure
analysis, and documentation unless explicitly assigned a disjoint
implementation lane. AI attribution belongs in contributor, commit, handoff,
review, and release records rather than repeated source-line watermarks.

The project originally shipped as LiveCD Creator: a Gambas3 GUI backed by
Bash scripts. That implementation (`.src/`, `.hidden/`) was retired from
this repository on 2026-07-25 at Kevin's explicit request and archived,
verified byte-identical (content, checksums, and permissions including
executable bits), to `/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`.
Full git history is intact. The project (package, commands, config paths)
was renamed from LiveCD to LiveUSB the same day, also at Kevin's explicit
request. The later installed LiveCD Creator `3.13.93` package recovered from
the 2016 ubuntuDE virtual machine is preserved under
`legacy/live-cd-3.13.93-installed/`. Version `3.13.93` is the last known
shipped behavioral reference, while the complete 2015 archive is the
source-level structural reference used for the Python translation. This
repository remains the only active product implementation.

Read `STATUS.md`, `ROADMAP.md`, `CONTRIBUTORS.md`, and
`docs/reviews/phase1d-2016-reconciliation.md` before material work.
The dated `docs/history/python-port.md` preserves the
architecture map, including the mapping from the retired original's
scripts/forms to their Python counterparts, and the original risk register.
**The port has never been run against a real ISO** — treat the first real
remastering run as the actual acceptance test.

## Hard constraints

1. **Config *format* compatibility is non-negotiable; the config *path* is
   not.** Inside the target filesystem, this tool reads and writes
   `etc/casper.conf`, `etc/lsb-release`, `ISO/isolinux/gfxboot.cfg`,
   `usr/share/desktop-base/grub_background.sh` in the same `KEY=value` line
   format the original Bash/Gambas tool used — that format is real Ubuntu
   convention, not this project's own naming, and must stay byte-compatible.
   The tool's *own* config directory, by contrast, is deliberately
   `/etc/live-usb` now (was `/etc/live-cd` before the 2026-07-25 rename) —
   see `docs/history/python-port.md` §7 item 5. An existing
   `/etc/live-cd` installation from the original LiveCD Creator is **not**
   picked up automatically; there is no migration path by design, since the
   port has never shipped and there is no real install in the field to
   protect.
2. **Deliberate divergences get documented.** If you fix a bug or change
   behaviour relative to the last shipped `3.13.93` package, record the
   current decision in `STATUS.md` or a current design document and preserve
   the historical evidence unchanged. The 2015 archive supplies source-level
   structure where the later Gambas source is unavailable. Accepted Python
   safety behavior outranks unsafe legacy mechanics. Version `3.13.93`
   remains the behavioral compatibility baseline for on-disk state inside
   the target filesystem, not this tool's own config path per constraint 1.

## Environment facts

- Backend actions need **root** — they chroot, loop-mount, and unmount.
- Real end-to-end testing needs an Ubuntu ISO plus `unsquashfs`, `mksquashfs`,
  `rsync`, `genisoimage`, `qemu-system-x86_64`, `Xephyr`.
- The GUI needs PyGObject + GTK3 (`gir1.2-gtk-3.0`). It can be smoke-tested
  headlessly under Xvfb using the recipe in the historical handoff.
- Targets Python 3.8+, stdlib only apart from PyGObject for the GUI.

## Commands

```bash
# Compile check (fast, catches most edits)
python3 -m py_compile liveusb/*.py liveusb/backend/*.py liveusb/gui/*.py

# CLI
bin/live-usb --help
sudo bin/live-usb -e     # extract   -r rebuild   -c chroot   -t clean

# GUI
bin/live-usb-gui
```

The tracked test suite currently contains `12` modules. The accepted
root-free baseline passes `298/298` tests with GUI support and `297` tests
with one expected GUI assertion skipped without PyGObject. Phase 1D legacy
final-image generation, mutation, hashing, and crash-durable publication are
root-free accepted. Phase 1E-A adds observation-only dependency, custody,
capacity, media-inspector, QEMU/KVM, resource, and operation-plan findings
without granting factory authority or executing factory commands. Phase
1E-B1 adds bounded version evidence and descriptor-bound source-media profile
inspection without CLI integration or factory authority. Phase 1E-B2 retains
capacity policy, exact factory commands, receipt persistence, CLI integration,
and the authorization handoff. Real privileged and end-to-end acceptance
remains pending.
