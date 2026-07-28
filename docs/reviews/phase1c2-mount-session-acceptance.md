# Phase 1C-2 mount-session candidate evidence

- Date: 2026-07-28
- Candidate parent: `67d9d11305c5b4206801b677ab48e67ab8d7b856`
- Authority: isolated Jacob Codex correction lane
- Independent Claude acceptance: not yet granted

## Scope

This candidate governs caller mounts, extraction ISO mounts, host X access,
temporary staging artifacts, operation-created directories, and recovery
metadata through one machine-wide operation lock.

The correction changes two product modules, three test modules, and three
control documents. Production remained read-only.

## Corrected findings

### B1: inferred mount ownership

The pending-journal transition contract now represents recovery-only inferred
ownership. The candidate must encode exactly the positive identity delta
between immutable pre-state and observed post-state. Every inferred owned
record must be at stage `owned`. Missing, additional, mixed, or tampered
ownership fails closed while preserving journal and pending evidence.

### B2: ISO source lifetime

New ISO acquisition validates the current configured source and captures its
identity before the mount command. Stale recovery validates the durable source
schema, plan semantics, exact owned mount identity, mount-root chain,
destination, and journal chain without reading the old source path.

Cleanup therefore remains possible after the ISO is deleted, moved, changed
in mode, replaced, or superseded by a different current configuration.
Corrupt custody or changed mount evidence remains fail-closed.

### B3: operation-created directories

Directory creation uses a random transaction-owned sibling staging path.
Recovery records the parent identity, desired final mode, staging path,
staging inode, final path, final inode, and lifecycle stage. Creation applies
the final mode explicitly before atomic rename, which removes dependence on
the process umask.

Recovery covers interruption before staging creation, after staging creation,
after staging identity persistence, after rename, and during cleanup.
Foreign, nonempty, replaced, mounted, symlinked, or identity-mismatched
evidence is preserved.

The remaining boundary is the documented trusted-single-writer model. The
random staging namespace and machine-wide lock are not a defense against a
hostile same-user process with arbitrary descriptor access.

### B4: ISO destination confinement

Durable ISO custody requires an absolute normalized destination that is an
exact direct child of the recorded literal mount root. Trailing separators,
dot forms, parent traversal, nesting, foreign paths, root aliasing, symlinked
ancestors, and replaced ancestor identities are rejected before mutation.

### B5: xhost entry grammar

The parser retains the exact C-locale status line and known-family rule.
Known families accept any nonempty, whitespace-free payload, including
resolved names and named IPv6 zones. Empty payloads, whitespace, unknown
families, duplicate status lines, malformed status, and formatting damage
remain rejected.

## Preserved accepted behavior

- Exact mountinfo identity and topology checks remain active.
- ISO cleanup remains `umount -f`.
- Other characterized mount cleanup remains unchanged.
- ISO mountpoints remain private mode `0700`.
- Effective mount semantics include `nosymfollow`.
- Extract cleanup remains one attempt per invocation.
- The cleanup-attempt flag resets after stale recovery initializes the new
  transaction.
- Cleanup failure preserves durable evidence for a fresh retry.
- Runtime custody remains outside the work tree with mode and ownership
  checks.

## Validation matrix

| Gate | Result |
| --- | ---: |
| Focused normal tests | `119/119` |
| Focused core-only tests | `119/119` |
| Complete normal suite | `208/208` |
| Complete core-only suite | `207` pass, `1` expected skip |
| Python 3.8 grammar | `46/46` |
| Product imports | `37/37` |
| Core-only imports | `23/23` |
| Harmless process smokes | `4/4` |
| Real privileged or graphical operations | `0` |

## Residue and limitations

Expected final runtime residue is:

- final journals: `0`;
- pending journals: `0`;
- active transaction locks: `0`;
- staged artifacts: `0`;
- staging directories: `0`;
- owned mount identities: `0`;
- session-owned X grants: `0`.

The operation lock inode may remain as managed infrastructure and does not
represent an active lock.

Native Python 3.8 execution remains untested because Python 3.8 is unavailable
on the validation host. Python 3.8 grammar validation passes. Real root,
mount, unmount, chroot, package, network, ISO, X, Xephyr, QEMU, and GUI
acceptance operations remain outside this candidate run.
