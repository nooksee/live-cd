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

- Python source files passing syntax compilation: `35/35`.
- Importable discovered modules: `34/34`.
- CLI help and version paths returning successfully: `2/2`.
- Current tracked test files: `0`.
- Successful remaster cycles produced by the Python implementation: `0`.
- Successful QEMU boots of Python-generated media: `0`.
- Real-desktop GUI acceptance passes: `0`.

The archived translation-session evidence records one prior Xvfb
window-construction pass across the translated GUI. That is implementation
evidence, not current product acceptance.

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

1. There is no automated test suite.
2. Extract and rebuild are hard-wired to the older single-SquashFS and
   `isolinux` media layout.
3. Mount and chroot cleanup are not transaction-safe across every failure.
4. Operational privilege handling still reflects the older root-process
   model.
5. Invalid operational arguments can reach privileged configuration
   initialization before clean rejection.
6. The GUI has not completed real-desktop product acceptance.

## Current acceptance gate

Phase 1 is the only active product gate:

> Extract the known-good 2015 `ubuntuDE` ISO, apply no customization, rebuild
> it with the Python implementation, and boot the result successfully in
> QEMU while leaving residual mounts, locks, and blocked files at `0`.

See `ROADMAP.md` for scope, team lanes, exclusions, and later phases. See
`docs/history/python-port-handoff-20260725.md` only for historical
translation mapping, prior verification evidence, and the inherited risk
register.
