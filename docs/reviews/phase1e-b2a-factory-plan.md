# Phase 1E-B2A: factory-plan and authorization contract

- Date: 2026-08-04
- Status: root-free implementation accepted locally; independent review
  return unavailable
- Product or privileged commands executed: `0`
- CLI or GUI wiring added: `0`

## Purpose and boundary

Phase 1E-B2A converts accepted Phase 1E-A observations and Phase 1E-B1
runtime evidence into one immutable plan for one bounded next operation. The
supported operation scopes are:

1. legacy source extraction;
2. legacy final-image assembly and publication;
3. BIOS QEMU boot of one validated publication pair.

The plan records exact direct external command arguments, capacity policy,
source and executable custody, a deterministic evidence digest, a
deterministic plan digest, and a scoped grant identifier. Planning itself is
root-free and executes every planned command `0` times.

No current CLI or GUI path consumes a grant. B2A does not authorize the
kernel-preparation and target-package lifecycle that precedes final-image
assembly in `run_rebuild`. Phase 1E-B2B must bind those remaining lifecycle
steps, recollect evidence immediately before execution, consume one grant
once, and persist the actual execution receipt before any real factory run.

## Fresh evidence rule

The caller-supplied Phase 1E-A report is not trusted as current state. At
plan time, the planner performs a fresh Phase 1E-A observation and compares
every finding relevant to the selected operation. A changed workspace,
mount, lock, journal, publication pair, media profile, source, or path-custody
finding refuses authority.

Phase 1E-B1 evidence is independently revalidated:

- the source ISO is reopened without following a final symlink;
- its descriptor identity and SHA-256 are checked before and after reading;
- its stable artifact identifier is `sha256:<digest>:size:<bytes>`;
- every required executable must remain the same literal, single-link,
  executable regular file observed by B1;
- executable owner, group, mode, size, timestamps, device, inode, and link
  count are included in the identity digest;
- successful bounded-process termination must be positively confirmed;
- the known `unsquashfs -version` nonzero result is accepted only when its
  expected version output matched.

The planner does not hold executable descriptors across a later privilege
boundary. Any state change after planning revokes the grant. B2B must repeat
the complete plan and identity comparison immediately before command
execution; it may not treat a stored receipt as current authority.

## Capacity policy

Extraction and final-image planning use this minimum free-space policy:

```text
max(32 GiB, source_size * 12 + max(4 GiB, source_size * 2))
```

The effective available value is the lower of the Phase 1E-A observation
and a new `statvfs` observation made by the planner. If both values are not
available, sufficiency is unresolved and authority is refused. BIOS QEMU
planning requires no additional factory-workspace allocation and therefore
uses a requirement of `0` bytes.

This policy is an explicit conservative threshold, not a mathematical upper
bound on arbitrary SquashFS expansion. The receipt records
`mathematical_upper_bound: false`. A later media-aware capacity model may
replace it only with its own evidence and tests.

## Exact command ownership

Command construction now has one source of truth. Pure builders in
`mounts.py`, `extract.py`, `rebuild.py`, and `qemu.py` are used by both the
accepted operation code and the B2A planner. Planner-specific copies of the
argument arrays are prohibited.

The bounded direct-command scopes are:

- extraction: source mount, SquashFS extraction, target architecture
  observation, ISO-tree copy, and forced ISO unmount;
- final-image assembly: bounded compressor probe, one SquashFS build,
  package manifest query, ISO generation, and legacy `isohybrid` mutation;
- QEMU: one BIOS CD-ROM boot command for one validated ISO and SHA-256 pair.

Dynamic mount-session recovery commands remain governed by the accepted
mount identity journal. They are not guessed before their mount identities
exist. B2B must preserve that boundary when it adds execution.

## Binding and refusal rules

Late-bound mount, probe, and publication paths must be normalized absolute
paths in their exact accepted roots. Probe source and output paths must share
one absent, direct-child probe root. A pre-existing or symlinked probe root
refuses authority. Existing outputs, path escapes, unsafe volume-label
fields, unresolved compression capability, missing tools, missing
`isohybrid`, changed source bytes, stale executable identity, insufficient
capacity, or any fresh-preflight mismatch all produce a refused plan with
commands `0` and no grant identifier.

## Receipt contract

The durable receipt is recursively immutable and ASCII JSON. It contains:

- operation, decision, refusal reasons, plan and evidence digests;
- the stable source artifact identifier;
- symbolic command paths such as `${WORK_DIR}` and `${TOOL:mount}`;
- minimized tool identity digests, statuses, and version lines;
- capacity formula, requirement, effective availability, and sufficiency;
- explicit command and privileged-operation execution counts of `0`.

It excludes raw stdout, raw stderr, descriptor numbers, device and inode
values, host paths, credentials, authentication state, and private session
content. Persistence uses a mode-`0600` pending file, file and directory
`fsync`, no-clobber hard-link publication, and cleanup of incomplete pending
evidence. Aliased, foreign-owned, or group/other-writable receipt directories
are rejected.

## Validation

- focused runtime and factory-plan tests: `57/57`;
- mount, recovery, caller, extraction, and rebuild regression tests:
  `177/177`;
- complete GUI-capable suite: `326/326`;
- complete core-only suite: `325` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `54/54`;
- product and core-only imports: `40/40` and `26/26`;
- harmless CLI process smokes: `4/4`;
- real root, sudo, mount, unmount, chroot, package, product-ISO, QEMU,
  Xephyr, and GUI operations: `0`.

Final residue after validation:

- generated bytecode files removed: `54`; remaining: `0`;
- generated cache directories removed: `4`; remaining: `0`;
- task temporary roots: `0`;
- runtime lock entries: `0`;
- pending receipts, journals, candidates, backups, and blocked files: `0`;
- factory helper processes: `0`.

## Review accounting

Three read-only delegated review lanes were attempted during implementation.
Completed returns were `0`; delegated file changes, privileged operations,
and persistent impact were `0/0/0`. No independent-acceptance claim is made.

The local adversarial pass found and closed seven issues before integration:

1. planner mutation of the global exclude-file constant;
2. fabricated unmount identity and incorrect lazy ISO-unmount arguments;
3. stale Phase 1E-A report acceptance;
4. a symlinkable compression-probe root;
5. mutable nested plan and receipt evidence;
6. zero-progress receipt writes;
7. receipt publication and cleanup races against a foreign target.

One attempted host-architecture modernization caused `1` regression-test
failure and was reverted. The final safety regression matrix passes
`177/177`; persistent effect from the rejected change is `0`.

## Next gate

Phase 1E-B2B must:

1. add the approved CLI preflight and planning surface;
2. bind the complete kernel-preparation and chroot lifecycle;
3. recollect A, B1, and B2A evidence under one operation boundary;
4. atomically consume one matching grant once;
5. execute only the exact revalidated plan;
6. persist actual command outcomes and cleanup evidence;
7. keep installation of the missing `isohybrid` dependency and the first
   real factory cycle behind a separately recorded operational gate.
