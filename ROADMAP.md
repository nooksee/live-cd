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

Work:

1. Create characterization tests for configuration parsing, CLI dispatch,
   media recognition, command construction, and checksum generation.
2. Isolate subprocess execution enough to inspect and test command plans.
3. Add dependency, privilege, disk-space, media-layout, and workspace
   preflight reporting.
4. Make mount, chroot, lock, temporary-file, and blocked-file cleanup
   deterministic under success and injected failure.
5. Correct immediate launch and argument-handling failures.
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
