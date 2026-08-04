# LiveUSB Creator roadmap

- Updated: 2026-08-04
- Current phase: Phase 1 — recover the legacy factory
- Current version: `0.1.0.dev0`

## Product mission

LiveUSB Creator is an operator-facing Ubuntu distribution workshop:

> Select an Ubuntu-family base, unpack it safely, customize identity,
> packages, desktop, files, hooks, and boot presentation, rebuild bootable
> media, and test the result.

The product should make the complete workflow understandable without
removing expert escape hatches. Modernization should preserve that intent
while replacing obsolete media assumptions, unsafe lifecycle behavior, and
unverified direct translations.

## Version policy

The project uses Python-compatible developmental release identifiers:

- `.devN` — engineering baseline before an alpha claim;
- `aN` — an incomplete product with at least one accepted end-to-end path;
- `bN` — planned capabilities complete enough for broader operator testing;
- `rcN` — release candidate with no known release-blocking defect;
- `1.0.0` — first stable, documented, supported product release.

Current version `0.1.0.dev0` makes no working-product claim. Completion of
Phase 1 promotes the project to `0.1.0a1`. Later version promotions require
evidence from their own acceptance gates rather than calendar dates.

## Team model

- **Kevin Thomas:** product intent, priorities, and final acceptance.
- **George Prime:** orchestration, bounded assignments, integration,
  acceptance evidence, and release coordination.
- **Claude Devens:** behavioral recovery, architecture and risk review,
  adversarial failure analysis, and independent phase-gate challenge.
- **Jacob Codex:** bounded implementation, tests, fixtures, mechanical
  validation, and evidence-backed closeout.

One active writer owns each code or documentation surface. Analysis and
testing may proceed concurrently when their surfaces do not overlap.

## Phase 1 — Recover the legacy factory

Objective:

> Complete one no-change round trip using the known-good 2015 `ubuntuDE`
> media: extract, rebuild, validate, and boot in QEMU.

Accepted Phase 1A baseline:

- commit `877036d`;
- Python 3.8 grammar and import validation: `39/39` and `39/39`;
- characterization tests: `15/15`;
- process smokes: `3/3`;
- invalid arguments rejected before configuration, privilege, or action
  effects;
- original scripts and Python backend modules compared: `11/11` and `13/13`.

Accepted Phase 1B baseline:

- fidelity correction commit `93a1d9e`;
- canonical VRAM default consumers: `4/4`;
- bounded EFI-content cases: `6/6`;
- unit tests with GUI support: `26/26`;
- core-only unit tests: `25` pass and `1` expected GUI skip;
- transaction review source coverage: `18/18`;
- transaction failure cases and invariant categories: `11` and `6/6`.

Accepted Phase 1C-1 baseline:

- implementation commits `47805d1`, `cc05ad5`, and `ed9aaba`;
- focused chroot-transaction tests: `43/43`;
- complete GUI-enabled suite: `69/69`;
- core-only suite: `68` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar validation: `42/42` and `42/42`;
- import and process-smoke validation: `36/36` and `4/4`;
- actual child-process crash and live-lock proofs: `2/2`;
- post-recovery residue categories: `0/0/0/0/0/0`.

Accepted Phase 1C-2 baseline:

- accepted implementation commit: `62a5191`;
- focused mount-session and extraction-recovery tests: `124/124`;
- complete GUI-enabled suite: `213/213`;
- core-only suite: `212` pass and `1` expected GUI skip;
- real privileged operation categories exercised: `0`;
- blocking, major, and minor review findings: `0/0/0`.

Accepted recovered-reference baseline:

- installed version: `3.13.93-0ubuntu3`;
- preserved package/reference files: `57`;
- SHA-256 checks: `57/57`;
- 2015-to-2016 backend delta: `13` changed, `2` identical, `5` added;
- added actions: `theme`, `plymouth`, `ubiquity`, `usb`, and `burn`;
- accepted review:
  `docs/reviews/phase1d-2016-reconciliation.md`.

Accepted Phase 1D root-free baseline:

- implementation commits: `2d34aea`, `e4746bc`, and `aa9e1f5`;
- independent oracle cases: `16/16`;
- blocking and major review findings: `0/0`;
- minor review findings closed before integration: `2/2`;
- focused finalization tests: `26/26`;
- complete GUI-capable suite: `239/239`;
- core-only suite: `238` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `48/48`;
- product and core-only imports: `37/37` and `23/23`;
- real bounded compressor outcomes: `xz` accepted and invalid compression
  rejected;
- real privileged and product-image operation categories exercised: `0`.

Accepted Phase 1E-A observation baseline:

- focused normal and core-only preflight tests: `30/30` and `30/30`;
- complete GUI-capable suite: `269/269`;
- core-only suite: `268` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `50/50`;
- product and core-only imports: `38/38` and `24/24`;
- privilege, dependency, source, custody, publication, capacity, inspector,
  QEMU/KVM, resource, and operation-plan findings remain independent;
- aggregate verdicts and factory authorization issued: `0`;
- real privileged and product-image operation categories exercised: `0`.

Accepted Phase 1E-B1 runtime-evidence baseline:

- implementation and review-closure commits: `7983722` and `99f5a50`;
- focused normal and core-only tests: `29/29` and `29/29`;
- complete GUI-capable suite: `298/298`;
- core-only suite: `297` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `52/52`;
- product and core-only imports: `39/39` and `25/25`;
- accepted real version outcomes: `8` success, `1` nonzero, and `1` absent;
- one synthetic ISO inspected by both providers with residue `0`;
- CLI integration and factory authorization issued: `0`.

Completed root-free work:

- one preplanned SquashFS command selected by a bounded capability probe;
- explicit legacy-media profile recognition;
- final `isohybrid` mutation before read-only sealing;
- final-byte SHA-256 evidence;
- crash-durable pair publication and narrow post-seal recovery;
- one operation lock across generation, mutation, evidence, and publication.
- observation-only factory preflight with sanitized JSON and text evidence.
- bounded version evidence and descriptor-bound source-media profile
  inspection without factory authority.

Remaining work:

1. Complete Phase 1E-B2 capacity requirements, exact factory command
   construction, minimized receipt persistence, stable descriptor
   representation, termination observability, executable-custody policy,
   CLI integration, and the authorization handoff.
2. Install or otherwise provide the missing `isohybrid` dependency only
   after root-free implementation and review acceptance.
3. Execute the legacy extract and no-change rebuild in a bounded workspace.
4. Validate ISO structure, preserve the input byte-identically, and boot the
   output in QEMU.
5. Reconcile the recovered 2016 customization actions after the first
   successful factory cycle. Device-writer launchers remain last.

Acceptance:

- automated test suite passing;
- legacy extraction success: `1`;
- no-change rebuild success: `1`;
- QEMU boot success: `1`;
- mutated input files: `0`;
- residual mounts, locks, blocked files, and abandoned temporary roots:
  `0/0/0/0`;
- exact commands, logs, output hashes, failures, and deviations recorded.

The rebuilt `md5sum.txt` is sorted while the original shell implementation
used filesystem traversal order. Acceptance compares the set of checksum
records or the individual payload hashes, not raw manifest byte order.

Excluded from Phase 1:

- modern Ubuntu media implementation;
- broad GUI redesign;
- plugin or recipe architecture;
- public stable-release claims;
- OSAI-specific product behavior.

## Phase 2 — Build the modern media engine

Replace fixed `isolinux` and single-SquashFS assumptions with detected media
profiles. Support layered live filesystems, preserve hybrid BIOS and UEFI
boot metadata, use current image tooling, and prove a no-change round trip
against current Ubuntu media.

## Phase 3 — Create the customization recipe system

Represent product identity, package changes, file overlays, hooks, desktop
selection, boot presentation, and validation as inspectable and repeatable
project recipes. Preserve direct expert access while making intended changes
auditable and reproducible.

## Phase 4 — Rebuild the operator application

Place a modern interface over the accepted engine. Provide workspace
lifecycle, preflight, progress, visible logs, interruption handling,
recovery, recipe editing, and QEMU validation without running the entire
graphical application as root.

## Phase 5 — Productize and broaden

Package the application, document supported hosts and media families,
establish continuous testing and release evidence, validate real hardware,
and define extension boundaries. Specialized Ubuntu variants, including
future OSAI builds, may become recipes or outputs without constraining the
general LiveUSB Creator architecture.
