# LiveUSB Creator status

- Updated: 2026-08-04
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
  Python 3.8 grammar validation: `56/56`.
- Importable product modules: `41/41`.
- Harmless CLI process smokes returning expected statuses: `5/5`.
- Current tracked test modules: `14`.
- Current unit tests passing with GUI support installed: `365/365`.
- Core-only unit tests passing without optional PyGObject: `364`, with the one
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

## Phase 1E-A observation-only preflight

Phase 1E-A adds an unwired, observation-only preflight engine. It reports
independent pass, fail, warning, unknown, and skipped findings without an
aggregate verdict or factory authorization. Current observations cover:

- literal workspace, source-ISO, lock, journal, and publication custody;
- descriptor-safe source SHA-256 evidence and mutation detection;
- dependency discovery with versions explicitly deferred;
- current privilege, sudo path presence, and absent factory authority as
  separate facts;
- exact capacity facts with sufficiency left unresolved;
- `isoinfo` preference with `xorriso` fallback readiness;
- architecture-consistent QEMU discovery, KVM accessibility, and TCG
  fallback;
- raw CPU, load, memory, and swap evidence without readiness thresholds;
- the accepted factory stage order with command construction and execution
  deferred to Phase 1E-B;
- deterministic sanitized evidence in both JSON and text output.

Phase 1E-A executes version queries, ISO inspection, QEMU, mount, package,
factory, and privileged commands `0` times. It is not wired into the CLI or
GUI and grants factory authority `0` times.

## Phase 1E-B1 bounded runtime evidence

Production commits `7983722` and `99f5a50`, merged through pull request `#4`,
add the provider-neutral runtime evidence layer without granting factory
authority. The accepted implementation provides:

- one explicit whitelist for ten bounded version-query tools;
- executable discovery constrained to the same fixed path used by the child
  process environment;
- finite positive timeout validation, bounded aggregate output, disabled
  standard input, no shell, and process-group termination;
- descriptor-bound source-ISO inspection with identity and SHA-256
  revalidation before and after inspection;
- `isoinfo` as the preferred inspector and `xorriso` as the fallback;
- fail-closed handling for source mutation, replacement, symlinks, and hard
  links;
- retained matching version evidence for a nonzero command without
  reclassifying that command as successful;
- deterministic sanitized evidence with factory authority fixed at `0`.

Accepted real root-free evidence records version-query outcomes of `8`
success, `1` nonzero, and `1` absent. The nonzero result is
`unsquashfs -version`, which prints version `4.6.1` while returning status
`1`; the absent dependency is `isohybrid`. One synthetic ISO of `366,592`
bytes was inspected by both providers and removed with residue `0`.

Phase 1E-B1 focused tests pass `29/29` in both normal and core-only postures.
Complete suites pass `298/298` with GUI support and `297` with one expected
GUI assertion skipped without PyGObject. Syntax and Python 3.8 grammar pass
`52/52`; product and core-only imports pass `39/39` and `25/25`.

At its acceptance boundary, Phase 1E-B1 remained unwired from the CLI and
granted factory authority `0` times. Its deferred capacity, command,
receipt, descriptor, termination, executable-custody, and authorization
questions are resolved for the B2A planning scope below.

## Phase 1E-B2A root-free factory planning

Phase 1E-B2A adds an unwired, root-free planner for one bounded next
operation: legacy extraction, legacy final-image assembly, or BIOS QEMU boot.
Production commit `549e616`, merged through pull request `#6`, provides:

- fresh Phase 1E-A recollection at grant time, with changed operation
  findings refusing authority;
- descriptor-bound source revalidation and the stable source identifier
  `sha256:<digest>:size:<bytes>`;
- literal executable identity, ownership, mode, link-count, and positive
  process-termination requirements;
- one shared command-builder source for planning and accepted operation code;
- the explicit capacity threshold
  `max(32GiB,source_size*12+max(4GiB,source_size*2))`, recorded as a
  conservative policy rather than a mathematical upper bound;
- recursively immutable plan and receipt evidence;
- symbolic, minimized, no-clobber receipt persistence;
- commands `0` and grant identifier `0` whenever any required fact is
  missing, stale, ambiguous, unsafe, or insufficient.

The accepted focused matrix passes `57/57`; the safety-sensitive regression
matrix passes `177/177`; complete suites pass `326/326` with GUI support and
`325` with one expected GUI skip without PyGObject. Real root, sudo, mount,
unmount, chroot, package, product-ISO, QEMU, Xephyr, and GUI operations remain
`0`.

Syntax and Python 3.8 grammar pass `54/54`; product and core-only imports pass
`40/40` and `26/26`; harmless CLI process smokes pass `4/4`.

B2A has no CLI or GUI consumer and executes planned commands `0` times. It
does not authorize the kernel-preparation and target-package lifecycle in
`run_rebuild`. The complete contract and remaining B2B boundary are recorded
in `docs/reviews/phase1e-b2a-factory-plan.md`.

## Phase 1E-B2B root-free factory execution acceptance

Phase 1E-B2B adds the complete-rebuild CLI and one-use execution boundary.
The accepted command surface is exactly:

```text
factory plan rebuild --records-dir ABSOLUTE_DIRECTORY
factory execute rebuild --grant ABSOLUTE_GRANT_DIRECTORY
factory recover rebuild --grant ABSOLUTE_GRANT_DIRECTORY
```

Planning recollects fresh Phase 1E-A, B1, and B2A evidence and binds target
distribution, architecture, kernel, mount, chroot, compression, image, and
publication authority into one private grant. Every issued grant has a fresh
session token and publication nonce. A stable record-directory lock spans
fresh recollection, consumption, execution, cleanup, and outcome persistence.
Authorized child processes inherit the lock lease so parent death cannot
release operation custody while a child remains alive.

Execution requires an already-root process. It compares a complete fresh plan
with the stored grant, revokes stale authority before commands run, moves a
matching grant from `issued` to `consumed` before the first command, and
permits only the exact authorized command grammar. Dynamic unmount and service
restoration remain governed by their accepted transaction journals. Recovery
accepts only a consumed grant and performs cleanup or publication recovery;
it cannot restart the rebuild or replay a factory command.

Durable states are `issued`, `consumed`, `revoked`, `succeeded`, `failed`, and
`interrupted`. Receipt publication is no-clobber and terminal state is
reconcilable after an interrupted state write. The legacy `-r` and `--rebuild`
flags are disabled before configuration or action side effects.

The atomic guarantee applies to one-use grant consumption, state and outcome
evidence, operation-lock custody, and the accepted final ISO/SHA-256 pair
publication. Target `apt` and `dpkg` mutations inside the extracted filesystem
are intentionally recorded as not fully rollbackable. The complete rebuild
is therefore controlled and crash-recoverable, but it is not represented as
one globally rollbackable filesystem transaction.

Root-free acceptance passes:

- focused factory-execution tests: `29/29`;
- review-closure runtime, factory, and mount-recovery tests: `117/117`;
- complete GUI-capable suite: `365/365`;
- core-only suite: `364` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `56/56`;
- product and core-only imports: `41/41` and `27/27`;
- harmless CLI process smokes: `5/5`.

Claude Devens independently reviewed the merged execution boundary and returned
`Accept with closure items`: confirmed behavioral defects `0`, high findings
`0`, medium findings `0`, low-medium test gaps `2`, low test gaps `1`, and
informational findings `2`. The closure matrix now proves different-valid-token
mount recovery rejection, changed-workspace revocation with commands executed
`0`, and pre-mutation rejection of a symlinked grant bundle, hard-linked state,
and symlinked factory lock.

Jacob Codex then found that the fixed evidence-probe path omitted
`/usr/sbin/chroot` on the acceptance host. The trusted path is now exactly
`/usr/sbin:/usr/bin:/sbin:/bin`; ambient operator `PATH` remains excluded. A
real bounded root-free query resolves `/usr/sbin/chroot`, returns GNU coreutils
`9.4`, confirms process termination, and grants factory authority `0` times.

The separate host dependency gate installed `syslinux-utils`
`3:6.04~git20190206.bf6db5b4+dfsg1-3ubuntu3`. Ubuntu `isohybrid -V` emits the
absolute executable name as `/usr/bin/isohybrid version 0.12`, while the
original fixture emitted only `isohybrid version 0.12`. Runtime evidence now
accepts both exact forms without changing executable discovery or custody. A
real bounded query passes with return code `0`, confirmed termination, and
factory authority `0`.

Real root, sudo, mount, unmount, chroot, package, product-ISO, QEMU, Xephyr,
and GUI operations remain `0`. The complete contract and next operational
gate are recorded in `docs/reviews/phase1e-b2b-cli-execution.md`.

## Immediate blockers

1. Extract and rebuild remain hard-wired to the older single-SquashFS and
   `isolinux` media layout.
2. The independently reviewed B2B execution boundary has completed root-free
   acceptance but has not completed one controlled real legacy rebuild.
3. Caller-level mount and host X-access safety has completed root-free
   acceptance but has not completed real privileged acceptance.
4. Operational privilege handling still reflects the older root-process
   model.
5. The GUI has not completed real-desktop product acceptance.

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
