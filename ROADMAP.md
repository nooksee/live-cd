# LiveUSB Creator roadmap

- Updated: 2026-07-26
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

Work:

1. Extend the accepted characterization baseline beyond configuration
   parsing, CLI dispatch, and checksum generation to media recognition,
   command construction, and injected failure behavior.
2. Isolate subprocess execution enough to inspect and test command plans.
3. Add dependency, privilege, disk-space, media-layout, and workspace
   preflight reporting.
4. Complete Wave 1C-2 of the accepted transaction contract from
   `docs/reviews/phase1b-transaction-safety.md`: caller-level mounts and X
   access. Wave 1C-1 chroot-internal state is accepted in
   `docs/reviews/phase1c1-chroot-transaction-acceptance.md`.
5. Correct remaining launch and argument-handling failures. Full argument
   prevalidation is complete.
6. Execute the legacy extract and no-change rebuild in a bounded workspace.
7. Validate ISO structure, preserve the input byte-identically, and boot the
   output in QEMU.

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
