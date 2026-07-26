# Phase 1A fidelity review

- Date: 2026-07-26
- Reviewer: Claude Devens
- Integration owner: George Prime
- Method: read-only static comparison
- Files modified by the review: `0`
- Root, mount, chroot, package, network, ISO-extraction, and QEMU operations:
  `0`

## Scope

The review compared:

- original hidden scripts: `11/11`;
- original Gambas forms and classes relevant to the workflow;
- translated Python backend modules: `13/13`;
- shared configuration helpers and three GUI configuration writers.

Cubic was not used as a specification. Modernization design was outside this
review.

## Accepted findings

| Finding | Classification | Current disposition |
| --- | --- | --- |
| Shared `KEY=value` configuration behavior is retained through one Python parser. | Preserved | Characterized by Phase 1A tests. |
| Python exceptions replace shell-wide halt-on-error behavior for declared application errors. | Preserved with adapted mechanism | Additional injected failure tests remain required. |
| The original `base_installable` work-directory path defect remains present and documented in source. | Confirmed inherited defect | Open. |
| EFI output selection searches filenames for `vmlinuz.efi`, while the original searched file contents across the media tree. | Confirmed Python-introduced defect | Open and release-blocking for fidelity. |
| The original default VRAM value is `2048`; the translated default is `1024`. | Confirmed Python-introduced defect | Open. |
| Unknown arguments reached configuration initialization before rejection. | Confirmed Python-introduced defect | Resolved by commit `877036d`. |
| Rebuild checksum records are sorted, while the original used filesystem traversal order. | Behavioral divergence | Accepted as deterministic behavior with a comparison caveat. |
| Physical-media checksum records use absolute paths and unsorted traversal, unlike the original relative-path form. | Confirmed divergence | Open, outside the first no-change ISO gate. |
| Effective user identity replaces the original real-user identity check. | Defensive divergence | Untested with actual privilege paths. |
| An empty distribution identifier now returns early instead of continuing. | Defensive divergence | Untested with real media. |

## Workflow contract

The preserved operator sequence is:

1. select and validate an input image;
2. extract the image into separate filesystem and media trees;
3. customize the extracted filesystem;
4. rebuild manifests, compressed filesystem, kernel, initrd, checksums, and
   bootable image;
5. boot-test the generated image;
6. leave mounts, locks, blocked files, and abandoned temporary roots at zero.

The current wrapper obtains elevated execution before backend actions. The
backend functions generally rely on that wrapper rather than repeating the
privilege check.

## Failure contract

The original and translated implementations share one serious lifecycle
defect: chroot operations do not have a transaction boundary around mounts,
the chroot lock, temporary host-file substitutions, and blocked service
files.

If an operation fails between setup and restoration, later cleanup calls may
never run. The current blunt recovery path can remove the entire work
directory, but no accepted surgical rollback exists.

Phase 1 therefore requires:

- setup and restoration paired through unconditional cleanup;
- residual mounts: `0`;
- residual lock files: `0`;
- residual blocked files: `0`;
- abandoned temporary roots: `0`;
- original input mutation: `0`.

Interactive error handling also retains the original blocking pause. Its
behavior under unattended and non-interactive execution remains untested.

## Legacy acceptance contract

The smallest accepted fidelity proof remains:

> Extract the known-good 2015 `ubuntuDE` ISO, apply no customization, rebuild
> with the Python implementation, boot the result in QEMU, preserve the input
> byte-identically, and leave residual mounts, locks, blocked files, and
> abandoned temporary roots at `0/0/0/0`.

Do not compare the rebuilt `md5sum.txt` byte-for-byte with an original rebuild.
The implementations order records differently. Compare the set of
path-and-hash records or compare each payload file independently.

## Ranked risks

1. Failure-unsafe chroot, mount, lock, and blocked-file lifecycle.
2. Incorrect EFI kernel-name detection.
3. Privilege and root-process architecture.
4. Incomplete characterization of media and command construction.
5. VRAM default drift from `2048` to `1024`.
6. Unattended blocking on interactive error pauses.
7. Physical-media checksum path divergence.
8. Rebuild checksum ordering as an acceptance-comparison trap.
9. Untested empty-distribution defensive return.
10. Untested effective-user privilege behavior.

## Quantitative result

- Preserved behaviors confirmed: `7`
- Confirmed defects: `5`
- Other divergences: `4`
- Untested risk classes: `4`
- Review protocol deviations: `0`
