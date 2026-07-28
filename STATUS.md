# LiveUSB Creator status

- Updated: 2026-07-28
- Current version: `0.1.0.dev0`
- Release maturity: pre-alpha recovery baseline

## Product identity

The current product name is **LiveUSB Creator**. No release-generation numeral
is part of the name.

This repository carries forward the GPL-licensed Customizer and LiveCD
Creator lineage while beginning a new Python product lifecycle under Kevin
Thomas. Current human and AI stewardship is recorded in `CONTRIBUTORS.md`.

`0.1.0.dev0` means that implementation recovery is active and no alpha
acceptance claim has been made. The first successful legacy
extract → no-change rebuild → QEMU boot cycle promotes the project to
`0.1.0a1`.

## Verified baseline

- All Python files, including package initializers, passing syntax and
  Python 3.8 grammar validation: `48/48`.
- Importable product modules: `37/37`.
- Harmless CLI process smokes returning expected statuses: `4/4`.
- Current tracked test modules: `10`.
- Current unit tests passing with GUI support installed: `239/239`.
- Core-only unit tests passing without optional PyGObject: `238`, with the one
  GUI-specific assertion skipped.
- Successful remaster cycles produced by the Python implementation: `0`.
- Successful QEMU boots of Python-generated media: `0`.
- Real-desktop GUI acceptance passes: `0`.

The archived translation-session evidence records one prior Xvfb
window-construction pass across the translated GUI. That is implementation
evidence, not current product acceptance.

## Phase 1A evidence

Commit `877036d` establishes the first characterization suite and corrects
CLI argument validation. Unknown arguments now return status `2` before
configuration, privilege, or action side effects. Mixed valid and invalid
arguments execute actions `0` times. Valid action order and duplicates remain
preserved.

The suite currently characterizes:

- CLI help, version, rejection, ordering, and duplicate dispatch;
- configuration quoting, unquoting, replacement, and default insertion;
- deterministic checksum generation and `md5sum.txt` exclusion.

Claude Devens completed a read-only fidelity comparison over original scripts
`11/11` and Python backend modules `13/13`. The review confirmed five defects,
four other behavioral divergences, and four untested risk classes. The
accepted findings and legacy acceptance caveat are recorded in
`docs/reviews/phase1a-fidelity-review.md`.

## Phase 1B evidence

Commit `93a1d9e` establishes one canonical VRAM default of `2048` across the
configuration, backend context, and GUI fallback paths. It also replaces EFI
kernel-name selection by pathname with a bounded, overlap-safe content scan
that matches the original case-sensitive behavior and continues past read
failures.

The new tests cover:

- all four VRAM default consumers;
- positive, negative, nested, misleading-filename, read-failure, and
  chunk-boundary EFI cases;
- GUI-present and CLI-only dependency environments.

Claude Devens completed a read-only transaction-safety review covering planned
files `18/18`, failure-injection cases `11`, and required invariant categories
`6/6`. George Prime accepted the evidence with two design corrections:
pre-existing host files must be restored exactly, and caller-level mount and
X-access safety requires explicit caller migration rather than zero call-site
changes. The accepted contract is recorded in
`docs/reviews/phase1b-transaction-safety.md`.

## Phase 1C-1 evidence

Commits `47805d1`, `cc05ad5`, and `ed9aaba` implement and harden the
chroot-internal transaction boundary. The accepted implementation provides:

- exact restoration for `hosts`, `resolv.conf`, `debian_chroot`, and `mtab`;
- an OS-held lock with persisted PID and token identity;
- an external, atomically replaced recovery journal;
- stale-transaction recovery before replacement ownership begins;
- explicit blocked-file stages and fail-closed replacement classification;
- ordered cleanup findings with primary-error chaining;
- reserved-path, path-confinement, orphan-evidence, and retry protection.

Focused transaction tests pass `43/43`. Actual child-process crash recovery
and live-lock rejection pass `2/2`. Post-recovery backups, journals, locks,
blocked files, transaction symlinks, and transaction-created lock directories
are `0/0/0/0/0/0`.

The implementation invokes real root, mount, chroot, package, network, ISO,
QEMU, and GUI operations `0` times during this acceptance. Native Python 3.8
execution and non-POSIX operation remain untested or unsupported. The
acceptance record is
`docs/reviews/phase1c1-chroot-transaction-acceptance.md`.

## Media evidence

The known 2015 `ubuntuDE` ISO matches the translated engine's assumptions:

- one `casper/filesystem.squashfs`;
- `isolinux/`;
- `initrd.lz`;
- the older Casper manifest layout.

The inspected Ubuntu 26.04 desktop ISO does not match those assumptions:

- `isolinux/` is absent;
- boot metadata is GRUB-based hybrid BIOS and UEFI;
- the media contains multiple layered and language-specific SquashFS images;
- the single `casper/filesystem.squashfs` contract is absent.

The current engine therefore cannot claim modern Ubuntu media support.

## Recovered 2016 behavioral reference

The installed LiveCD Creator `3.13.93-0ubuntu3` package recovered from the
2016 ubuntuDE virtual machine is now preserved as immutable historical
evidence under `legacy/live-cd-3.13.93-installed/`.

Validation passes:

- captured package/reference files: `57/57`;
- preservation SHA-256 checks: `57/57`;
- package MD5 checks: `17/17`;
- conffiles: `35/35`;
- installed package-list entries: `108/108`.

An independent 2015-to-2016 comparison reproduces `13` changed overlapping
files, `2` byte-identical files, and `5` added action files. The recovered
build also adds final `isohybrid` mutation and an ISO SHA-256 sidecar. Phase
1D now implements and accepts those two behaviors for the bounded legacy-media
profile while preserving the current Python safety authority.

The accepted authority model is:

- version `3.13.93` is the last known shipped behavioral reference;
- the complete 2015 source is the source-level structural reference;
- accepted Python transaction and recovery behavior is current safety
  authority.

The evidence, classifications, deferrals, and corrected implementation order
are recorded in
`docs/reviews/phase1d-2016-reconciliation.md`.

## Phase 1C-2 root-free acceptance

The accepted implementation at `62a5191` provides a machine-wide
mount-session transaction for caller mounts, ISO extraction mounts, host X
access, temporary artifacts, and operation-created directories. Recovery uses
exact mountinfo identities, atomic journals, a private runtime lock, positive
mount deltas, tokenized directory staging, exact parent and inode custody, and
bounded cleanup.

Focused mount-session and extraction-recovery tests pass `124/124` in both
normal and core-only postures. Complete suites pass `213/213` with GUI
dependencies and `212` with one expected GUI assertion skipped without
PyGObject. Real root, mount, unmount, chroot, package, network, ISO, X,
Xephyr, QEMU, and GUI operations performed during root-free acceptance remain
`0`.

The directory-recovery correction covers initial interruption after private
mkdir, after final-mode chmod, during recovery before changed state is
durable, and during staged or created directory removal. Planned staging
accepts only an empty, unmounted tokenized directory at the desired mode or a
permission-subset of private mode `0700`. Owner-inaccessible private submodes
are normalized only for inspection, with the original mode restored when
foreign content is found. Unproved permission bits and foreign location,
content, mount, or identity evidence remain fail-closed.

Claude Devens independently reviewed a physically read-only `62a5191`
checkout and returned `ACCEPT`, with `0` blocking, `0` major, `0` minor, and
`4` informational findings. The acceptance certifies the root-free recovery
design and evidence. Real privileged acceptance remains pending. The detailed
record is `docs/reviews/phase1c2-mount-session-acceptance.md`.

## Phase 1D root-free acceptance

Production commits `2d34aea`, `e4746bc`, and `aa9e1f5` implement and prove
the recovered legacy final-image contract:

- one bounded synthetic SquashFS capability probe before one product-tree
  SquashFS build;
- explicit, literal, non-symlink legacy-media profile recognition;
- `isohybrid` mutation before read-only sealing;
- final-byte SHA-256 evidence using the ISO basename;
- one operation lock spanning SquashFS generation through publication;
- crash-durable candidate, prior-pair, backup, and publication custody;
- narrow recovery after sealing without repeating kernel work, SquashFS,
  `genisoimage`, or `isohybrid`;
- fail-closed handling for altered, foreign, non-regular, hard-linked,
  path-escaping, or ownership-mismatched evidence.

Claude Devens independently traced oracle cases `16/16` and returned
`ACCEPT`, with `0` blocking and `0` major findings. Two minor test-posture
findings were closed before integration: the real compressor probe now runs
by default when `mksquashfs` is available, and a real hard-link rejection
case is included.

Production acceptance passes:

- focused finalization tests: `26/26`;
- complete GUI-capable suite: `239/239`;
- core-only suite: `238` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `48/48`;
- product and core-only imports: `37/37` and `23/23`;
- harmless CLI process smokes: `4/4`;
- real default-suite compressor outcomes: `xz` accepted and an invalid
  compressor rejected;
- runtime locks, journals, pending journals, candidates, backups, probe
  files, and temporary residue: `0`.

Real root, sudo, mount, unmount, chroot, package, product-ISO, QEMU, Xephyr,
and GUI operations remain `0`. Actual power-loss behavior, a complete product
ISO build, native Python 3.8 execution, and the privileged factory cycle
remain untested.

## Immediate blockers

1. Characterization coverage does not yet include media recognition or broad
   command construction.
2. Extract and rebuild remain hard-wired to the older single-SquashFS and
   `isolinux` media layout.
3. The current host does not have the `isohybrid` command required for the
   recovered legacy finalization path.
4. Caller-level mount and host X-access safety has completed root-free
   acceptance but has not completed real privileged acceptance.
5. Operational privilege handling still reflects the older root-process
   model.
6. The GUI has not completed real-desktop product acceptance.

## Current acceptance gate

Phase 1 is the only active product gate:

> Extract the known-good 2015 `ubuntuDE` ISO, apply no customization, rebuild
> it with the Python implementation, finalize and hash the output according
> to the accepted legacy-media contract, and boot the result successfully in
> QEMU while leaving residual mounts, locks, and blocked files at `0`.

See `ROADMAP.md` for scope, team lanes, exclusions, and later phases. See
`docs/history/python-port.md` only for historical
translation mapping, prior verification evidence, and the inherited risk
register. See `docs/reviews/phase1a-fidelity-review.md` for the
accepted behavioral comparison and checksum-comparison caveat. See
`docs/reviews/phase1b-transaction-safety.md` for the accepted cleanup
contract and implementation boundaries. See
`docs/reviews/phase1c1-chroot-transaction-acceptance.md` for the accepted
chroot-internal transaction evidence and remaining Wave 1C-2 boundary. See
`docs/reviews/phase1d-2016-reconciliation.md` for the recovered 2016
behavioral authority, accepted delta ledger, and next root-free gate.
