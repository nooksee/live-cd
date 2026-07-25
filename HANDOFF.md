# Handoff: the Python port

Written at the end of the porting session, for whoever picks this up next
(human or assistant). It covers what was built, what was actually verified,
where the bodies are buried, and what to do first.

**2026-07-25 update:** the original Gambas3 GUI (`.src/`) and Bash backend
(`.hidden/`) that this was ported from have been retired from this
repository at Kevin's request and archived, verified byte-identical, to
`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`. Full git history is
intact (the retirement was later folded into a `git-filter-repo` rewrite —
see git log for the current commit that first excludes `.src`/`.hidden`).
This is now the only implementation in the repository; the `python/` prefix
used throughout the rest of this document has been flattened away, and
paths below are relative to the repo root.

**2026-07-25 update, same day:** the project itself was renamed from LiveCD
Creator to **LiveUSB Creator** at Kevin's request — "it will be live-usb and
LiveUSB for naming". The Python package is now `liveusb/` (was `livecd/`),
the CLI/GUI commands are `live-usb`/`live-usb-gui` (were `live-cd`/
`live-cd-gui`), and the on-disk config paths are `/etc/live-usb` and
`/home/live-usb` (were `/etc/live-cd` and `/home/live-cd` — see §7, this is
a real compatibility break with any existing `/etc/live-cd` install, not
just cosmetic). Historical references to the literal old filenames
(`.hidden/live-cd.sh`, etc.) are left as-is below since that's what the
archived original was actually called.

---

## 1. State in one paragraph

Every Bash script that used to live in `.hidden/` and every Gambas form that
used to live in `.src/` has a Python counterpart under `liveusb/`. All 35
modules byte-compile. The CLI's argument handling and the GUI's window
construction were both exercised and behave correctly. **No ISO has ever been
remastered with this code.** The environment it was written in had no Ubuntu
ISO, no loop-mount capability, and no `mksquashfs`/`genisoimage`. So the
backend is a carefully reviewed translation, not a proven one. Treat the first
real remastering run as the actual acceptance test.

---

## 2. Getting it running

```bash
# GUI dependency (Debian/Ubuntu)
sudo apt-get install gir1.2-gtk-3.0 python3-gi

# Backend tooling, if not already present
sudo apt-get install squashfs-tools rsync genisoimage qemu-system-x86 \
                     xserver-xephyr imagemagick pv

# Run in place, no install needed
bin/live-usb --help
bin/live-usb-gui

# Or install properly
pip install -e .[gui]
```

`python3-gi` and the GTK typelib must be visible to the *same* interpreter.
A common failure is a pyenv/conda Python that can't see the distro's
`/usr/lib/python3/dist-packages/gi`. If `import gi` fails there, use the
system interpreter or symlink the bindings in.

---

## 3. Architecture map

Everything is a direct 1:1 translation. If you're wondering "where did X go",
it's here.

### Backend — formerly `.hidden/scripts/*` → `liveusb/backend/*`

| Original script | Port | Notes |
| --- | --- | --- |
| `extract` | `extract.py` | Mount ISO, unsquash, rsync the rest |
| `cdimage` | `cdimage.py` | `dd` a physical disc to ISO |
| `chroot` | `chroot_shell.py` | Named to avoid clashing with `chroot.py` |
| `clean` | `clean.py` | |
| `deb` | `deb.py` | |
| `gui` | `gui_install.py` | Named to avoid clashing with the `gui/` package |
| `hook` | `hook.py` | |
| `pkgm` | `pkgm.py` | |
| `qemu` | `qemu.py` | |
| `rebuild` | `rebuild.py` | Longest and most intricate; see risk register |
| `xnest` | `xnest.py` | |

`.hidden/common` split by concern:

| Bash function group | Port |
| --- | --- |
| `__mount_sys__`, `__umount_sys__`, `__recursive_umount__`, `__mount_dbus__`, `__check_lock__`, `__check_fs_dir__`, `__check_for_X__`, `__allow/__block_local_X_access__`, `__purge_work_dirs__` | `backend/mounts.py` |
| `__chroot__`, `__update_distro_name__`, `__create_work_dirs__`, `__check_sources_list__` | `backend/chroot.py` |
| `INFO_MESSAGE` / `WARNING_MESSAGE` / `ERROR_MESSAGE` family | `messages.py` |

`.hidden/live-cd.sh` → `cli.py` + `bin/live-usb`. Same flags, same multi-flag
looping, same `su` elevation via `Root_it` → `root_it()`.

### GUI — formerly `.src/F*.class` + `.form` → `liveusb/gui/*`

| Gambas form | Port |
| --- | --- |
| `FMain` | `main_window.py` |
| `FSettings` | `settings_window.py` |
| `FGrub2` | `grub2_window.py` |
| `FSysLinux` | `syslinux_window.py` |
| `FTweaks` | `tweaks_window.py` |
| `FPackages` | `packages_window.py` |
| `FDownloader` | `downloader_window.py` |
| `FAbout` / `FCredits` / `FLicense` | `about_window.py` / `credits_window.py` / `license_window.py` |
| `Check` module | `gui/checks.py` (logic) + `gui/app.py` (the `Main()` elevation) |
| `Func` module | split → `config.py`, `fsutil.py`, `messages.py` |

### Why `Func` was split three ways

The Gambas `Func` module mixed three unrelated jobs. They're now:

- `config.py` — `Get_Str` / `Replace_Str` / `Replace_Str_AsIs`. The
  `KEY=value` line format that every config file in this project uses.
  **This is the compatibility-critical module.**
- `fsutil.py` — load/save file, and the "search a hardcoded list of binaries"
  detection for text editors, terminals, and file managers.
- `messages.py` — console output.

---

## 4. Design decisions worth knowing

**`Context` instead of sourced globals.** The Bash scripts all began with
`source /etc/live-cd/default`, giving them `$WORK_DIR`, `$COMPRESSION`, etc.
as globals. The port loads those once into a `Context` dataclass
(`backend/__init__.py`) that gets passed down. `ctx.fs_dir` and `ctx.iso_dir`
are computed properties, so `$WORK_DIR/FileSystem` is never re-derived by
hand.

**Errors raise, they don't exit.** Bash's `ERROR_MESSAGE` printed and called
`exit 2`. The port's `messages.error()` raises `LiveUSBError`, caught in
`cli.py`'s `root_it()` which then exits 2. This keeps the backend importable
and testable — a library that calls `sys.exit` is miserable to test. If you
add a new entry point, catch `LiveUSBError`.

**GUI shells out to the CLI.** Same as the original: the Gambas GUI ran
`terminal -e "live-cd.sh -e"`. The port does the same via
`resources.find_cli_executable()`, which checks the dev-checkout `bin/`, then
`/usr/bin`, then `PATH`. The GUI deliberately does **not** import the backend
directly — that preserves the original's model where long operations run
visibly in a terminal the user can watch and interrupt.

**`resources.py` handles dev-vs-installed paths.** Icons live at the repo root
(`icons/`, `live-usb.svg`) in a checkout, but at `/usr/share/live-usb` once
packaged. Everything goes through `resources.find_pixmap()` so both work.
(This module's dev-checkout path math assumed the pre-flatten
`repo_root/python/liveusb/` nesting and quietly broke — `app_icon_path()`
returned `None` in a checkout — when `python/` was flattened away earlier
today. Fixed alongside this rename; see §7 item 5.)

---

## 5. What was actually verified

Be precise about this, because "it's done" and "it works" are different
claims.

**Verified:**

- All 35 modules byte-compile.
- CLI: no-args usage, `-h`, `-v`, unrecognised-argument error path, and
  `--clean` against a work directory (correctly reported nothing to clean).
- Config layer: `ensure_config_exists()` writes a correct default file;
  `get_str`/`replace_str` round-trip.
- GUI: **every** window class instantiates under Xvfb with GTK 3.24 —
  main, settings, grub2, syslinux, tweaks, packages, downloader, about,
  credits, license. The main window correctly loaded `DISTRIB_ID`,
  `DISTRIB_RELEASE`, `HOST`, `USERNAME` and the release-notes URL from a
  fake filesystem, and computed the right widget enable/disable states.

**One real bug was caught this way:** message colouring wasn't applied to the
CLI's first status line because config loaded after it printed. Fixed —
`main()` now loads config up front, mirroring `live-cd.sh` sourcing at the
top.

**Three more bugs were caught in later review passes** (see §7).

**Not verified — and this is the important part:**

- No extract → chroot → rebuild cycle has run. Not once.
- No chroot has been entered. `chroot_run()`'s block/unblock dance is
  entirely unexercised.
- No squashfs made, no ISO generated, nothing booted in QEMU.
- The GUI has never been seen on a real desktop — only constructed
  headlessly. Layout, sizing, and icon rendering are unreviewed.

---

## 6. Risk register

Ranked by "how likely is this to bite you on the first real run".

### High — `rebuild.py`

The longest module and the one that touches the most moving parts.

- **`mksquashfs` compression fallback changed shape.** The original grepped
  `mksquashfs -version` for `4.2`/`4.3` and dropped `-comp` if absent. The
  port instead *tries* with `-comp` and retries without it on failure. Same
  intent, different mechanism — but the retry runs a full squash again, which
  on a real filesystem is minutes wasted, and it will also retry on failures
  that have nothing to do with `-comp`. Worth reworking to check the version
  properly.
- **md5sum format.** The original ran `find . -type f ! -name md5sum.txt -exec
  md5sum '{}' +` from inside `$WORK_DIR/ISO`, producing `./relative/path`
  entries. The port reimplements this in Python and writes `./{rel}` to match.
  If the ISO fails to verify at boot, compare a generated `md5sum.txt` against
  one from the Bash version byte-for-byte — this is exactly the kind of thing
  that's subtly wrong.
- **`genisoimage` invocation** is a faithful copy of the flags, but has never
  been executed. The original wrapped it in a subshell with `zenity` and a
  `read` at the end; the port drops the subshell and makes `zenity` optional.
- **`vmlinuz` vs `vmlinuz.efi`** selection walks the whole ISO tree looking
  for the string `vmlinuz.efi` (the original used `grep -Rs`). Faithful, but
  slow and slightly fuzzy.

### High — `chroot.py::chroot_run()`

The block/unblock file dance is the most side-effecting code in the project
and has never run. It renames `initctl`, `update-grub`, and everything in
`/etc/init.d` to `.blocked`, symlinks them to `/bin/true`, runs your command,
then reverses it. **If it fails midway, the target filesystem is left with
`.blocked` files and dangling symlinks.** There is no transactional recovery —
same as the original, but the original had years of real-world use to shake
out its edges.

Note the original's unblock loop contains dead code (`_f` is assigned twice,
the first assignment discarded). The port implements what the code *does*, not
what that line suggests was intended.

### Medium — `xnest.py`

`Exec=` lines in `.desktop` files can contain field codes (`%U`, `%f`). The
original passed the whole string to an unquoted shell expansion; the port
parses with `shlex.split()` and strips `%`-codes. Cleaner, but if a session's
`Exec=` is unusual the behaviour will differ.

### Medium — GUI entry-change handlers

In `main_window.py`, the `changed` signal fires on **every keystroke**, and
each one writes to `lsb-release` / `casper.conf` / `issue`. The Gambas
original had the same behaviour, so this is faithful — but on Python it means
a file write per character typed. If it feels sluggish or you see partial
values written, debounce it or switch to `focus-out-event`. Note that
`refresh_existence()` sets entry text programmatically, which *also* fires
these handlers — worth confirming that doesn't write garbage during startup.

### Low — `cdimage.py`

Reads `/dev/cdrom` three times (label, copy, md5) exactly like the original.
Fine, just slow. Optical hardware is rare enough that this is unlikely to be
exercised at all.

---

## 7. Deliberate divergences from the original

Keep this list current — it's how the two implementations stay comparable.

1. **`hook` dropped a broken `exec`.** The original ran
   `chroot "$FS" env ... exec /tmp/HOOK`. Since `exec` is a shell builtin and
   not an executable, `env` would fail with *"env: 'exec': No such file or
   directory"* — meaning the hook feature was broken in the original. The port
   runs `/tmp/HOOK` directly. **Worth confirming against a real hook file**;
   if hooks demonstrably worked before, this analysis is wrong.
2. **GRUB colour spelling unified.** The Gambas combo box listed
   `dark-gray`/`light-gray` while its `Select Case` matched
   `dark-grey`/`light-grey`, so picking those two never updated the preview.
   The port uses one spelling. Affects the preview only, not what's written
   to `grub_background.sh`.
3. **`rebuild`'s `base_installable` check is preserved as-is**, including what
   looks like a path bug: it tests `$WORK_DIR/usr/bin/ubiquity` rather than
   `$WORK_DIR/FileSystem/usr/bin/ubiquity`. Since this changes on-disk output
   it was kept rather than silently "fixed". Decide deliberately.
4. **Two bugs found in the port itself during final review, now fixed** —
   recorded because they show the class of error to look for:
   - `extract.py` unmounted `MOUNT_DIR` rather than the `mktemp` subdirectory
     in its error paths, leaking a mounted temp dir. The Bash reassigns
     `MOUNT_DIR` to the temp path, which is easy to miss when translating.
   - `chroot.py` used `str.find("FileSystem")` where Bash used `${f##*FileSystem}`.
     Bash strips through the *last* occurrence; `find` matches the first. With
     a work directory like `/home/FileSystem/work`, the port would have created
     symlinks at the wrong path inside the chroot. Now uses `rfind`.
5. **Config paths renamed, not just cosmetics.** As part of the LiveCD →
   LiveUSB rename, `constants.ETC_DIR` changed from `/etc/live-cd` to
   `/etc/live-usb`, and `DEFAULT_WORK_DIR` from `/home/live-cd` to
   `/home/live-usb`. This was a deliberate choice, not an oversight: the
   port has never shipped or run against a real ISO, so there is no real
   `/etc/live-cd` install in the field to stay compatible with. If that
   assumption is ever wrong, an existing `/etc/live-cd` config will simply
   not be found — there is no fallback/migration path from the old
   directory, by design.
6. **A third self-introduced bug, found while doing the LiveUSB rename.**
   `resources.py`'s dev-checkout path math (`_GUI_DIR` → one `pardir` hop →
   `_REPO_ROOT`) was written for the pre-flatten `python/livecd/` nesting.
   When `python/` was flattened to the repo root earlier the same day, this
   math silently started resolving one directory too high
   (`_REPO_ROOT` landed on `Projects/` instead of `Projects/live-cd/`), so
   `app_icon_path()`/`app_png_path()` returned `None` in a dev checkout.
   Nothing exercises this path in `py_compile` or the CLI smoke test, which
   is why it wasn't caught immediately. Fixed by removing the now-redundant
   extra `pardir` hop.

---

## 8. Suggested first session

In order. The early steps are cheap and de-risk the expensive ones.

**Step 1 — Reproduce the GUI smoke test (5 min, no ISO needed).**

```bash
sudo apt-get install -y gir1.2-gtk-3.0 xvfb
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99

# Fake environment
sudo mkdir -p /etc/live-usb
mkdir -p /tmp/lcd/work/FileSystem/etc /tmp/lcd/work/FileSystem/{usr,root} \
         /tmp/lcd/work/ISO/.disk
printf 'DISTRIB_ID=CustomOS\nDISTRIB_RELEASE=1.0\nDISTRIB_CODENAME=test\n' \
  > /tmp/lcd/work/FileSystem/etc/lsb-release
printf 'export USERNAME="live"\nexport HOST="host"\n' \
  > /tmp/lcd/work/FileSystem/etc/casper.conf
echo 'http://example.com/notes' > /tmp/lcd/work/ISO/.disk/release_notes_url
printf 'WORK_DIR=/tmp/lcd/work\nMOUNT_DIR=/mnt\nMESSAGES_COLORS=1\n' \
  | sudo tee /etc/live-usb/default

sudo -E python3 -c "
import sys; sys.path.insert(0,'.')
from liveusb.gui.gtkcompat import Gtk
from liveusb.gui.main_window import MainWindow
mw = MainWindow(on_close=Gtk.main_quit)
print('distname:', mw.distname_entry.get_text())
print('build_iso enabled:', mw.build_iso_btn.get_sensitive())
"
```

Gotcha that cost time: if a previous run died, `/etc/live-usb/gui_lock` is left
behind and the next launch blocks on an "already running" dialog with no
window manager to dismiss it. `rm -f /etc/live-usb/gui_lock` between runs.

**Step 2 — Look at the GUI on a real desktop.** Just launch it. Check
layout, sizing, whether icons resolve. Nobody has ever seen these windows.

**Step 3 — Write characterisation tests for `config.py`.** Highest
value-per-minute in the project. It's pure, needs no root, and it's the
compatibility-critical piece. Feed it the real `casper.conf` / `lsb-release` /
`gfxboot.cfg` formats and pin the round-trip behaviour, especially quoting.
The Gambas `Quote`/`UnQuote` semantics were reimplemented by hand and deserve
to be nailed down.

**Step 4 — Differential test against the Bash original.** The strongest
correctness signal available without a full run: point both implementations at
identical copies of a work directory, run the same operation, `diff -r` the
results. Ideal for `config.py` edits (`DistName` change, timezone change) and
for `md5sum.txt` generation.

**Step 5 — The real thing.** Ubuntu Mini Remix is the smallest sensible
target. `live-usb -e`, then `-r`, then `-q` to boot it in QEMU. Expect
`rebuild.py` to need fixes. Keep the Bash version's output alongside for
comparison.

---

## 9. Things I'd do if continuing

- **A `--dry-run` flag** that logs every subprocess and filesystem mutation
  without performing it. For code this side-effecting, it would have made the
  whole port testable without root, and it's not much work — everything
  already funnels through `backend.run()`.
- **Replace the hardcoded binary lists** in `constants.py` with
  `shutil.which()` lookups. The original hunted for `/usr/bin/gedit` and
  friends by absolute path, which misses anything in `/usr/local/bin` or a
  Nix/Flatpak layout. Kept faithful for now, but it's a cheap modernisation.
- **The Ubuntu URLs in `downloader_window.py` are dead** — they point at
  14.04 and a 12.04 mini-remix. Faithful to the original, useless in practice.
  Worth repointing at current releases or fetching an index.
- **`README.md`** is the user-facing doc; this file is the
  contributor-facing one. Keep them from drifting.
