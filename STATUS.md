# LiveUSB Creator status

- Updated: 2026-07-26
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

- Python source files passing syntax and Python 3.8 grammar validation:
  `39/39`.
- Importable discovered modules: `39/39`.
- CLI help and version paths returning successfully: `2/2`.
- Current tracked test modules: `4`.
- Phase 1A unit tests passing: `15/15`.
- Phase 1A process smokes passing: `3/3`.
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

## Immediate blockers

1. Characterization coverage does not yet include media recognition, command
   construction, or injected transaction failures.
2. Extract and rebuild remain hard-wired to the older single-SquashFS and
   `isolinux` media layout.
3. Mount and chroot cleanup are not transaction-safe across every failure.
4. Operational privilege handling still reflects the older root-process
   model.
5. EFI kernel naming detection searches filenames where the original searched
   media contents, which can select the wrong output kernel name.
6. The translated default VRAM value is `1024`, while the original default is
   `2048`.
7. The GUI has not completed real-desktop product acceptance.

## Current acceptance gate

Phase 1 is the only active product gate:

> Extract the known-good 2015 `ubuntuDE` ISO, apply no customization, rebuild
> it with the Python implementation, and boot the result successfully in
> QEMU while leaving residual mounts, locks, and blocked files at `0`.

See `ROADMAP.md` for scope, team lanes, exclusions, and later phases. See
`docs/history/python-port.md` only for historical
translation mapping, prior verification evidence, and the inherited risk
register. See `docs/reviews/phase1a-fidelity-review.md` for the
accepted behavioral comparison and checksum-comparison caveat.
