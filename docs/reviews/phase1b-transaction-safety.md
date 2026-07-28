# Phase 1B transaction-safety review

- Date: 2026-07-26
- Reviewer: Claude Devens
- Disposition owner: George Prime
- Method: read-only static comparison
- Files modified by the review: `0`
- Root, sudo, mount, chroot, package, network, ISO, QEMU, and GUI operations:
  `0`

## Scope

The review covered:

- `liveusb/backend/chroot.py`;
- `liveusb/backend/mounts.py`;
- backend support and caller modules: `9/9`;
- archived shell lifecycle functions and callers;
- planned source files: `18/18`.

The review found transaction traps, `finally` blocks, and equivalent
failure-safe boundaries `0` times in both the archived implementation and the
translated backend.

## Accepted ownership findings

Current side effects have fragmented ownership:

- callers create `/dev`, `/proc`, and `/sys` mounts;
- selected callers create D-Bus mounts;
- selected callers grant host X access;
- `chroot_run` substitutes host files, writes the chroot marker, creates the
  `mtab` symlink and lock, and blocks service files;
- caller code manually attempts unmounting and X-access reversal after
  `chroot_run` returns.

An exception before those sequential cleanup calls can leave mounts, the
lock, substituted files, blocked files, symlinks, and host X access active.
The current recovery path can require wholesale work-directory deletion.

## Required invariants

Every accepted implementation must prove:

1. mounts created by one operation are released in reverse order;
2. the lock exists only while its owning transaction is active;
3. each blocked file is restored or deliberately replaced by an observed
   package update;
4. transaction-created symlinks do not survive cleanup;
5. pre-existing `hosts` and `resolv.conf` nodes are restored exactly,
   including absence, file content, or symbolic-link identity;
6. temporary roots and host X-access grants do not survive the owning
   operation.

Cleanup must be idempotent and must attempt every restoration step even when
an earlier cleanup step fails. Partial cleanup must produce an explicit error
that does not hide the primary operation failure.

## Failure matrix

Claude Devens identified failure-injection cases `11`:

1. partial system mount;
2. host-file substitution failure;
3. lock creation failure;
4. blocked-file rename followed by stub failure;
5. blocking failure after a partial candidate set;
6. nonzero chroot command;
7. missing chroot executable;
8. partial unblock failure;
9. partial temporary-directory cleanup;
10. failed unmount;
11. re-entry after stale state.

The current implementation already handles two classes acceptably: a nonzero
chroot command remains a warning while cleanup continues, and recursive
unmount remains an idempotent backstop. The other nine require new
transactional behavior.

## George Prime disposition

The context-manager direction is accepted with four corrections.

### Exact host-file restoration

The proposal to preserve unconditional deletion of extracted `hosts` and
`resolv.conf` is rejected. Phase 1 is a no-change round trip. A transaction
must capture and restore the exact pre-operation node state. If a node was
absent, the injected replacement is removed. If it was a file or symbolic
link, that state is restored.

### Mount ownership requires acquisition evidence

A mount session cannot claim ownership while the existing mount helpers
return no acquisition state. The mount layer must report whether each mount
was already present, created by this operation, or failed. Cleanup may
unmount only operation-owned mounts before invoking the explicit recursive
backstop.

### Caller migration is required

Zero call-site changes cannot provide caller-level mount safety. The seven
mount-using action modules must enter the accepted mount session so cleanup
runs when `chroot_run` raises. X-using callers must similarly pair host
X-access grant and revocation through a guaranteed lifecycle.

### Python 3.8 cleanup errors

Python 3.8 has no exception-group facility. Cleanup failures therefore require
one project-specific error carrying the ordered cleanup findings while
preserving any primary operation exception through exception chaining.
Every cleanup step still runs.

## Implementation sequence

### Wave 1C-1: chroot-internal transaction

Expected surfaces:

- new `liveusb/backend/transaction.py`;
- modified `liveusb/backend/chroot.py`;
- new `tests/test_transaction.py`.

This wave owns host-file snapshots, marker and symlink state, the explicit
lock lifecycle, blocked-file records, restoration, and cleanup error
reporting. It invokes root, mount, chroot, package, network, ISO, QEMU, and GUI
operations `0` times during tests.

### Wave 1C-2: caller operation session

Expected surfaces:

- new `liveusb/backend/mount_session.py`;
- modified `liveusb/backend/mounts.py`;
- the seven mount-using action modules;
- focused mount-session and caller tests.

This wave records acquired mounts, guarantees reverse-order cleanup, retains
recursive unmount as an explicit backstop, and guarantees revocation of any
host X-access grant.

The two waves remain separate so internal filesystem restoration can be
accepted before caller orchestration changes.

## Acceptance

Before any root-backed ISO operation:

- failure cases passing: `11/11`;
- invariant categories passing: `6/6`;
- residual mounts, locks, blocked files, transaction symlinks, host-file
  substitutions, temporary roots, and X-access grants:
  `0/0/0/0/0/0/0`;
- input mutation outside declared rebuild outputs: `0`;
- cleanup failures surfaced explicitly: `100%`;
- actual root operations during unit validation: `0`.

## Quantitative result

- Planned files covered: `18/18`
- Failure-injection cases: `11`
- Required invariant categories: `6/6`
- Existing acceptable failure behaviors: `2`
- Behaviors requiring transaction work: `9`
- Review assumptions: `1`
- Review protocol deviations: `0`
