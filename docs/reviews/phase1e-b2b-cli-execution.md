# Phase 1E-B2B: CLI execution and atomic grant contract

- Date: 2026-08-04
- Status: root-free implementation accepted locally; real factory cycle
  pending
- Real product or privileged operations executed: `0`
- Legacy rebuild bypasses retained: `0`

## Purpose and boundary

Phase 1E-B2B converts the accepted B2A plan into one consumable complete
rebuild authorization. It adds the CLI integration, binds the kernel and
chroot lifecycle that B2A intentionally deferred, executes only revalidated
authority, and persists one actual terminal outcome.

This phase does not claim that a real remaster cycle has passed. All automated
acceptance is root-free. The current host also lacks `isohybrid`, so a real
legacy finalization remains impossible until that dependency receives a
separate operational gate.

## Exact CLI surface

The accepted grammar is deliberately narrow:

```text
live-usb factory plan rebuild --records-dir ABSOLUTE_DIRECTORY
live-usb factory execute rebuild --grant ABSOLUTE_GRANT_DIRECTORY
live-usb factory recover rebuild --grant ABSOLUTE_GRANT_DIRECTORY
```

Each form requires exactly four operands after `factory`. The path must be
normalized and absolute. Planning may run without root. Execution and
recovery require an already-root process and never invoke `su`, `sudo`, or
another implicit elevation mechanism.

The legacy `-r` and `--rebuild` flags return status `2` before configuration
loading or action dispatch. Mixed legacy rebuild flags cannot execute another
valid action first. This closes the unplanned complete-rebuild bypass.

## Strict configuration boundary

Factory commands use strict configuration loading. It reads the existing
configuration and exclude files without creating defaults or rewriting
values. Missing files, duplicate keys, non-regular nodes, aliases, and
multi-linked files refuse the factory operation.

The existing compatibility loader remains available to non-factory legacy
actions. Factory strictness therefore does not silently change unrelated
legacy command behavior.

## Fresh authorization

Planning performs fresh Phase 1E-A observation, Phase 1E-B1 runtime evidence,
and B2A final-image planning under the factory record-directory lock. It also
binds facts that are specific to the complete rebuild:

- literal target `lsb-release` metadata and target package architecture;
- current target kernel and initramfs evidence;
- one exact kernel branch, either update or purge-and-install;
- literal `mount` and `umount` custody;
- bounded compressor capability;
- system-mount, chroot, manifest, SquashFS, ISO, hybrid-mutation, and
  publication authority;
- accepted transaction-journal authority for dynamic unmount and service
  restoration.

Every granted plan receives a fresh 32-hex session token and publication
nonce. The grant identifier is therefore unique to an issued authorization,
even when all observed host and project evidence is otherwise identical.

Immediately before execution, the engine recollects the complete plan using
the stored token and nonce. Any mismatch revokes the issued grant with
commands executed `0`. Stored evidence is never treated as current authority
by itself.

## One-use state machine

The private grant bundle contains an immutable grant, mutable state, and at
most one immutable outcome. The accepted phases are:

```text
issued -> consumed -> succeeded
                   -> failed
                   -> interrupted
issued -> revoked
```

The transition from `issued` to `consumed` is atomically persisted before the
first factory command. A consumed grant can never return to `issued` and can
never execute a second time. Repeated execute requests against consumed or
terminal state fail closed.

The record directory has one stable `flock` lease. It spans fresh evidence,
grant comparison, consumption, command execution, cleanup, and outcome
persistence. Every authorized subprocess inherits that lease descriptor.
Closing or losing the parent process therefore does not release the operation
lock while an authorized child remains alive.

Grant directories use mode `0700`; grant, state, lock, and outcome files use
mode `0600`. Atomic writes use file and directory synchronization. Outcome
publication is no-clobber. If the outcome becomes durable but the terminal
state replacement is interrupted, the next entry reconciles terminal state
from the matching immutable outcome without rerunning recovery or factory
commands.

## Exact execution authority

The executor permits only the frozen command and state sequence for one
authorization:

1. target architecture observation;
2. `/dev`, `/proc`, and `/sys` mount acquisition;
3. exactly one selected kernel branch;
4. chroot locale, service-block, package-helper, target, and cleanup stages;
5. compressor probe, one SquashFS build, manifest generation, ISO generation,
   legacy `isohybrid`, sealing, hashing, and pair publication;
6. reverse cleanup through journal-confirmed identities.

Architecture, mount order, kernel target, chroot prefix and environment,
service paths, cleanup grammar, tool paths, publication namespace, and every
B2A direct command are checked before execution. A changed argument, extra
command, reused stage, or incomplete command surface fails closed.

Dynamic recursive unmounts are not guessed as static command arrays. They
remain authorized only by positive mount identities in the accepted
mount-session journal. Service restoration remains authorized only by the
accepted chroot transaction journal.

## Recovery contract

`factory recover rebuild` accepts only a consumed grant. It enters the same
stable record lock and the same session and publication namespaces, then
performs mount, chroot, or publication recovery from durable journal evidence.

Recovery cannot start a new rebuild, repeat package operations, regenerate
SquashFS, rerun ISO generation, or consume a fresh command plan. Its replayed
factory-command count is structurally fixed at `0`. A successful recovery
from interrupted state produces an `interrupted` terminal outcome because the
original product result cannot be reconstructed safely after parent death.
The interrupted receipt explicitly records that original command outcomes are
unavailable. The grant still preserves the exact authorized command surface,
and recovery records only the cleanup commands it actually observes.

## Atomicity statement

The word atomic applies to these accepted boundaries:

- one-use grant consumption;
- durable grant-state transitions;
- no-clobber outcome publication and terminal reconciliation;
- stable operation-lock custody, including inherited child leases;
- the previously accepted crash-durable final ISO and SHA-256 pair
  publication.

It does not mean that the complete rebuild is one globally rollbackable
transaction. Target package operations can alter `apt` and `dpkg` state in
the extracted filesystem before a later failure. That mutation authority is
recorded explicitly as not fully rollbackable. Recovery restores mounts,
service substitutions, transaction files, and publication custody; it does
not invent a package-state rollback.

Grant files and state files are protected against concurrent product writers,
aliases, unsafe parent custody, malformed schemas, and inconsistent outcomes.
The grant owner can still delete or deliberately replace files that the same
operating-system account owns. Hostile same-user record tampering is outside
this phase and fails closed only where the remaining evidence exposes the
inconsistency.

## Receipt and privacy contract

Grant and outcome evidence use symbolic paths such as `${WORK_DIR}`,
`${FILESYSTEM_ROOT}`, `${ISO_ROOT}`, `${EXCLUDE_FILE}`, and `${TOOL:name}`.
Receipts include the authorized stage, symbolic argument vector, authority
class, return code, error type, residual counts, state, and digests required
for reconciliation.

They exclude raw stdout, raw stderr, credentials, authentication state,
pairing data, private session content, descriptor numbers, and raw private
host paths. Error evidence records the exception type, not arbitrary exception
text.

## Root-free validation

- focused factory-execution tests: `29/29`;
- review-closure runtime, factory, and mount-recovery tests: `117/117`;
- complete GUI-capable suite: `365/365`;
- complete core-only suite: `364` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `56/56`;
- product and core-only imports: `41/41` and `27/27`;
- harmless CLI process smokes: `5/5`;
- exact-scope and whitespace findings: `0`;
- protocol deviations: `0`.

The focused matrix proves stale-evidence revocation, private issuance,
one-use success, replay refusal, consumed recovery with factory commands `0`,
real child death after consumption, durable outcome reconciliation,
revocation reconciliation, terminal-outcome mismatch rejection, same-process
and forked lock contention, child-held lock custody, exact state-sequence
validation, interrupted pending-state reconciliation, complete command-surface
consumption, recovery-child lock custody, real stale-chroot recovery,
journal-only dynamic cleanup, deep immutability, publication namespace custody,
token rejection, and exception precedence.

Real root, sudo, mount, unmount, chroot, package, product-ISO, QEMU, Xephyr,
and GUI operations performed by this acceptance remain `0`.

## Independent review and closure

Claude Devens completed a read-only review of the merged B2B range. The review
read the changed production and test surfaces, independently ran the focused
CLI, configuration, and factory-execution set `47/47`, and returned `Accept
with closure items`. It found confirmed behavioral defects `0`, high findings
`0`, medium findings `0`, low-medium test gaps `2`, low test gaps `1`, and
informational findings `2`.

The required closure proves:

- a valid but different mount-session owner token cannot recover another
  grant journal;
- a changed canonical workspace revokes the grant with commands executed `0`;
- symlinked grant bundles, hard-linked state records, and symlinked factory
  locks are rejected before state mutation.

Jacob Codex independently inventoried first-cycle host readiness and found one
additional operational defect: the fixed probe path was `/bin:/usr/bin`, while
the accepted root-owned `chroot` binary is `/usr/sbin/chroot`. The corrected
trusted path is exactly `/usr/sbin:/usr/bin:/sbin:/bin`. It still ignores
ambient operator `PATH`. A real bounded root-free query now resolves
`/usr/sbin/chroot`, accepts GNU coreutils `9.4`, confirms process termination,
and grants factory authority `0` times.

The separate host dependency gate installed `syslinux-utils`
`3:6.04~git20190206.bf6db5b4+dfsg1-3ubuntu3`. The real Ubuntu command emits
`/usr/bin/isohybrid version 0.12`, including its absolute `argv[0]`, while the
historical fixture emits `isohybrid version 0.12`. Runtime evidence now accepts
both exact forms. The real bounded query returns success, confirms process
termination, and grants factory authority `0`.

The independent-review and dependency-evidence gates are closed. This
correction performs real root, mount, unmount, chroot, product-ISO, QEMU,
Xephyr, and GUI operations `0` times. The preceding separately authorized host
gate performs package installations `1`, upgrades `0`, and removals `0`.
Its protocol deviations are `1`, involving `2` task-owned diagnostic files
created through the wrong mechanism and then removed; persistent impact and
residue are `0`.

## Remaining operational gate

Before the first real complete rebuild:

1. issue a fresh plan against the preserved legacy source and workspace;
2. inspect the refused or granted receipt before execution;
3. execute one grant once under observation;
4. verify the output pair, residual state, and durable outcome;
5. boot the generated ISO in QEMU before any alpha promotion.
