# Phase 1C-2 mount-session root-free acceptance

- Date: 2026-07-28
- Accepted candidate: `62a5191d748f483f004060c916cdc4feede83b34`
- Stack base: `472a099730cc828fdcd1039a3bdc9d92d82e44cb`
- Final correction chain: `5485ff2` → `8fc7582` → `62a5191`
- Authority: isolated Jacob Codex correction lane with George Prime final
  audit correction
- Independent Claude acceptance: granted at `62a5191`

## Scope

This accepted root-free boundary governs caller mounts, extraction ISO
mounts, host X access, temporary staging artifacts, operation-created
directories, and recovery metadata through one machine-wide operation lock.

The accepted Phase 1C-2 stack changes `19` tracked paths. The final
directory-recovery correction changes exactly one product module, one test
module, and two control documents. Production remained read-only until
independent acceptance.

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

Recovery covers interruption before staging creation, after a
umask-filtered private mkdir, after final-mode chmod, after staging identity
persistence, after rename, during recovery before its changed candidate is
durable, and during cleanup at both the staging and final locations. A
durable `removing` record retains whether the exact inode is still at its
staging location or has reached its final location.

Planned-state recovery accepts only a literal, empty, unmounted tokenized
staging directory whose mode is the desired mode or a permission-subset of
private mode `0700`. Modes with unproved group or other bits remain rejected.
An owner-inaccessible private submode is normalized only long enough to inspect
the tokenized staging directory; foreign content causes the original mode to
be restored before recovery fails closed.
Foreign, nonempty, replaced, mounted, symlinked, wrong-location,
two-live-location, missing-identity, or identity-mismatched evidence is
preserved.

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

## Independent acceptance

Claude Devens inspected a physically read-only checkout of `62a5191` and
returned `VERDICT: ACCEPT`. The review found `0` blocking, `0` major, `0`
minor, and `4` informational findings. It confirmed that:

- all four defects from the prior rejection are resolved;
- the pre-rename directory-removal retry path is recoverable;
- owner-inaccessible private staging mode `0200` is handled safely;
- B1, B2, B4, B5, and F4 through F8 remain intact;
- fail-closed evidence preservation and the trusted-single-writer boundary
  remain explicit.

The informational findings are retained for later privileged acceptance:

- parent-directory fsync does not itself guarantee child-mode metadata
  durability, but both possible durable modes are recoverable;
- a crash before restoration can leave a foreign nonempty staging directory
  at the normalized mode, while content remains untouched and adoption remains
  rejected;
- the `PermissionError` normalization branch requires confirmation under a
  real root process;
- inferred-recovery delta ordering remains unconstrained while duplicates and
  wrong-order cleanup remain fail-closed.

Claude modified files `0`, ran tests `0`, performed Git mutations `0`, invoked
real-operation categories `0`, and reported protocol deviations `0`.

## Validation matrix

| Gate | Result |
| --- | ---: |
| Focused normal tests | `124/124` |
| Focused core-only tests | `124/124` |
| Complete normal suite | `213/213` |
| Complete core-only suite | `212` pass, `1` expected skip |
| Syntax and Python 3.8 grammar, all Python files | `47/47` |
| Product imports | `37/37` |
| Core-only imports | `23/23` |
| Harmless process smokes | `4/4` |
| Independent read-only disposition | `ACCEPT` |
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
acceptance operations remain outside this root-free acceptance.
