# LiveCD Creator 3.13.93 installed reference

Status: immutable historical reference; not active product source.

This directory preserves the installed LiveCD Creator package recovered from
the `ubuntuDE-2016-Reference` virtual machine on 2026-07-28. The package was
built on 2016-08-12 as version `3.13.93-0ubuntu3`.

## Contents

- Package payload files covered by the Debian MD5 manifest: `17`
- Debian conffiles under `/etc/live-cd`: `35`
- Debian package metadata files: `5`
- Total captured files, excluding this note and `SHA256SUMS`: `57`
- Package-owned Humanity and hicolor icons: `8`

The compiled GUI is `usr/bin/live-cd`, a Gambas `gbr3` archive built with
Gambas 3.8.90. Its application version is `3.13.93`.

- MD5: `b9fbcfa838ad4948e034a19f032cf8a8`
- SHA-256: `d516e987f0d8c29afb4358696d7c40ea0bd58cd417264f68d37d56cebf82bbb9`

The source ISO from which the virtual machine was installed is:

`/media/nos4r2/user_vol/Dropbox/DiskImages/ubuntuDE-amd64-14-DE-3.13.0-93.iso`

Its SHA-256 is:

`bcb82024633272c4330c675f24dde9333c27e766c750dcdfb3f3818b09bb2d8e`

## Validation

The preservation pass produced these results:

- Debian payload MD5 checks: `17/17`
- Installed package-list paths present: `108/108`
- Debian conffiles present: `35/35`
- Conffile byte comparisons against the guest capture: `35/35`
- Debian metadata byte comparisons against the guest capture: `5/5`

`SHA256SUMS` covers every one of the 57 captured files. It intentionally
excludes itself and this provenance note.

## Relationship to the Python product

The current Python implementation began from the complete 2015 Gambas and
Bash source archive. This installed 2016 package is a later behavioral oracle.
It changes thirteen overlapping core or action files, adds five actions
(`theme`, `plymouth`, `ubiquity`, `usb`, and `burn`), and adds sixteen
distribution-skeleton files.

The compiled Gambas archive confirms that the 2016 GUI exposed all five later
actions. It also records the later `isohybrid` and final ISO SHA-256 workflow
through its installed backend. Reconciliation into the Python product must be
deliberate and tested. Files in this directory must not be imported or executed
as current product code.

The complete 2015 source remains separately preserved at:

`/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/`

## Metadata limitation

The VirtualBox shared-folder transfer normalized copied permissions to `0776`.
For this Git reference, executable flags were restored only for the Gambas
launcher, command wrapper, backend scripts, and Debian lifecycle scripts.
Content integrity is established by the package MD5 manifest and the complete
SHA-256 manifest. The virtual-machine disk remains the source for exact
installed filesystem ownership and metadata. A powered-off VirtualBox snapshot
named `live-cd-3.13.93-evidence-baseline-20260728` preserves the validated guest
state.
