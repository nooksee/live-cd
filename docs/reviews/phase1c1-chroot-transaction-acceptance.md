# Phase 1C-1 chroot-transaction acceptance

- Date: 2026-07-26
- Status: accepted
- Product phase: Phase 1, Wave 1C-1
- Accepted implementation commits: `47805d1`, `cc05ad5`, `ed9aaba`

## Disposition

The chroot-internal transaction is accepted for Phase 1. It replaces the
sequential, interruption-vulnerable chroot setup and cleanup path with one
recoverable ownership boundary.

This acceptance covers chroot-internal state only. Caller-owned mounts and
host X-access grants remain Wave 1C-2 work. Root-backed ISO acceptance remains
unauthorized until both transaction waves pass their root-free gates.

## Accepted behavior

One transaction now owns:

1. exact prior state for `etc/hosts`, `etc/resolv.conf`,
   `etc/debian_chroot`, and `etc/mtab`;
2. one OS-held lock with persisted PID and token identity;
3. one external atomic recovery journal under the work-directory root;
4. blocked-service records with planned, renamed, stubbed, and
   preserve-replacement stages;
5. ordered cleanup findings and primary-error chaining;
6. stale-state recovery before replacement ownership begins.

Cleanup restores every managed node exactly, including prior absence, file
content and mode, or symbolic-link identity. A blocked service is restored
unless an `lstat` regular-file replacement is observed. Directories,
symbolic links, special nodes, malformed metadata, unexpected residue,
reserved targets, and path escapes fail closed while preserving recovery
evidence.

Exclusive `flock` ownership is authoritative. PID and token values identify
the journal and lock but do not veto recovery after the operating system has
granted the exclusive lock. This avoids a false stale-state hold after PID
reuse while still rejecting a live lock holder.

## Review and correction history

Claude Devens first identified the incompatibility between in-memory
snapshots and force takeover after process death. The review also identified
the four-node restoration scope and the blocked-file rename-before-stub
outcome.

Jacob Codex implemented the bounded transaction and durable recovery journal
in an isolated clone. George Prime reproduced actual dead-process recovery,
then found three fail-closed defects involving non-regular replacements,
pending-evidence deletion, and PID reuse. Claude Devens independently accepted
the architecture while requiring path-confinement and orphan-evidence proof.
The final hardening commit resolves the three reproduced defects and adds the
required proof surfaces.

## Validation

| Gate | Result |
| --- | ---: |
| Focused transaction tests | `43/43` |
| Complete suite with GUI dependencies | `69/69` |
| Core-only suite | `68` pass, `1` expected skip |
| Syntax validation | `42/42` |
| Python 3.8 grammar validation | `42/42` |
| Product-module imports | `36/36` |
| Harmless CLI process smokes | `4/4` |
| Actual child crash and live-lock proofs | `2/2` |
| Changed implementation paths in the accepted stack | `3` |

Post-crash and post-cleanup residue:

| Category | Count |
| --- | ---: |
| Managed-node backups | `0` |
| Final or pending journals | `0` |
| Lock files | `0` |
| Blocked files | `0` |
| Transaction-created service symlinks | `0` |
| Transaction-created lock directories | `0` |

Real root, sudo, mount, chroot, package, network, ISO, QEMU, and GUI
operations during acceptance were `0` each.

## Boundaries

- Native Python 3.8 execution remains untested because Python 3.8 is not
  installed on the validation host. Python 3.8 grammar passes `42/42`.
- The transaction uses POSIX `fcntl` locking. Non-POSIX environments remain
  unsupported.
- This wave does not claim mount ownership, reverse-order unmount, recursive
  unmount backstop orchestration, or host X-access reversal.
- Successful extraction, no-change rebuild, and QEMU boot counts remain
  `0/0/0`.

## Plant and custody accounting

The final serial closeout preflight at `2026-07-26T17:20:35-04:00` found:

- logical CPUs and load averages: `4` and `2.82/2.62/2.41`;
- available memory and used swap: `24 GiB` and `4.0 KiB`;
- process count: `327`;
- free space on `user_vol` and the system volume: `400 GiB/327 GiB`;
- one tracker process consuming approximately one core;
- execution mode: serial foreground closeout with parallel workers `0`.

LiveUSB active claims observed before integration were `0`. The shared
registry path was held by one non-overlapping OSAI III claim, so shared
registry files changed `0` times and cross-project path overlaps were `0`.
The exact LiveUSB integration claim and release are recorded by this
independent-repository acceptance record and final clean synchronization.

## Protocol accounting

Control verification protocol deviations: `4`.

1. One read-only compatibility search ran from the live checkout instead of
   the scratch clone.
2. One LiveUSB claim was recorded here instead of the occupied shared
   registry.
3. Two documentation patches were rejected before writing because their
   context anchors did not match the current file.

Persistent, source-loss, private-state, credential, payload, and external
impact from those deviations: `0/0/0/0/0/0`.

## Next gate

Phase 1C-2 must add caller-level operation sessions for mount acquisition,
reverse-order cleanup, recursive-unmount backstop behavior, and host X-access
revocation. That wave remains root-free until its focused and complete test
matrices pass.
