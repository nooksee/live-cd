# Phase 1D: LiveCD Creator 3.13.93 reconciliation

- Date: 2026-07-28
- Status: accepted root-free implementation; real factory acceptance pending
- Product-code paths changed by Phase 1D: `2`
- Test paths changed by Phase 1D: `2`
- Root, mount, chroot, package, ISO-build, QEMU, and GUI operations: `0`

## Authority

The recovered references now have distinct roles:

1. Kevin Thomas remains product-intent and final-acceptance authority.
2. LiveCD Creator `3.13.93-0ubuntu3`, built on 2016-08-12, is the last
   known shipped behavioral reference.
3. The complete 2015 Gambas and Bash archive is the source-level structural
   reference used for the original Python translation.
4. Accepted Python transaction, mount-session, path-confinement, cleanup,
   and argument-safety behavior is current safety authority and must not
   regress to match unsafe legacy mechanics.
5. A recovered behavior may be preserved, deliberately modernized, deferred,
   or rejected as a defect. Every non-fidelity decision requires evidence and
   an explicit record.

The current Python implementation remains the only active product source.
The two historical references are immutable evidence, not parallel products.

## Preserved evidence

The installed 2016 reference is preserved under:

`legacy/live-cd-3.13.93-installed/`

Project preservation commit:

`ba906cf93d1e0d7022fabe84a207bef175bf0b8a`

The upload artifact supplied to the original translation session has
SHA-256:

`65b9386854dd4bbb4c06664cc1c885b290e9cae9e1690df62443e14487b054c8`

The powered-off source virtual machine is preserved by the VirtualBox
snapshot:

`live-cd-3.13.93-evidence-baseline-20260728`

Evidence validation:

- captured package/reference files: `57`;
- preservation SHA-256 checks: `57/57`;
- Debian package-payload MD5 checks: `17/17`;
- Debian conffiles: `35/35`;
- installed package-list entries: `108/108`;
- Gambas executable size: `955173` bytes;
- Gambas executable SHA-256:
  `d516e987f0d8c29afb4358696d7c40ea0bd58cd417264f68d37d56cebf82bbb9`.

## Independently reproduced source delta

George Prime independently compared the preserved 2016 plain-text backend
against `/media/nos4r2/hard_vol2/LiveCD-Original-2015-Archive/hidden`.

| File class | Count |
| --- | ---: |
| Changed overlapping files | 13 |
| Byte-identical overlapping files | 2 |
| Added action files | 5 |
| Total compared or added files | 20 |

The byte-identical files are `default` and `exclude`.

The five added action files and recovered CLI flags are:

| Action | Flag |
| --- | --- |
| `theme` | `-b`, `--theme` |
| `plymouth` | `-l`, `--plymouth` |
| `ubiquity` | `-u`, `--ubiquity` |
| `usb` | `-w`, `--usb` |
| `burn` | `-f`, `--burn` |

The compiled Gambas archive remains a static behavioral oracle. Static
strings confirm all five later actions and the recovered handler names, but
they do not prove handler semantics. No binary execution or bytecode
decompilation occurred during this review.

## Accepted delta ledger

| Recovered 2016 behavior | Current classification | Execution phase |
| --- | --- | --- |
| ISO mutation through `isohybrid` | Implemented and root-free accepted for the legacy-media profile | Phase 1D accepted |
| Final ISO SHA-256 sidecar | Implemented with final-byte and basename validation | Phase 1D accepted |
| Stale SHA-256 sidecar cleanup | Replaced by crash-durable prior-pair preservation and publication custody | Phase 1D accepted |
| `mksquashfs` 4.2/4.3 version test | Replaced by a bounded synthetic capability probe | Phase 1D accepted |
| Retry-without-compression after any squash failure | Removed; one product-tree SquashFS invocation is enforced | Phase 1D accepted |
| Persistent `/var/log/live-cd.log` | Recovered behavior; host-global path is not accepted as the modern contract | Deferred logging design |
| `ZENITY_ERROR` with a bare shell `exit` | Recovered behavior with ambiguous or successful exit status; not accepted | Deferred operator-error adapter |
| Removal of `__check_sources_list__` | Current Python stub is dead and behaviorally inert | Deferred mechanical cleanup |
| `cdimage.iso` to `live-cd.iso` and SHA-256 sidecar | Recovered optical-media behavior | Deferred optical-media lane |
| 2016 desktop package lists and skeleton copying | Recovered customization behavior | Post-round-trip recovery |
| `theme`, `plymouth`, and `ubiquity` | Recovered customization actions | Post-round-trip recovery |
| `usb` and `burn` | Recovered external device-writer launchers | Final recovered-action lane |
| Additional GUI handlers and configuration keys | Static evidence with unresolved semantics | VM observation before implementation |
| `base_installable` path behavior | Confirmed legacy defect | Do not reproduce without explicit decision |
| `hook` invocation through `exec` | Empirically unresolved legacy behavior | VM experiment before decision |

## Corrected execution order

The historical translator recommended logging and the Zenity error adapter
before ISO finalization. That order is not accepted for product execution.
Neither behavior is required to prove the first no-change factory cycle, and
both need a modern design decision.

Phase 1D is restricted to the final-image path:

1. Characterize command planning, stale-output cleanup, ISO finalization,
   hashing, and failure behavior without root.
2. Select compression capability before creating the SquashFS image so one
   build attempt occurs.
3. Generate the ISO with the accepted legacy command plan.
4. Apply `isohybrid` while the output is still writable.
5. Seal the output read-only only after the final content mutation succeeds.
6. Hash the final bytes and publish the SHA-256 sidecar without exposing a
   partial sidecar as successful evidence.
7. Preserve all accepted transaction and mount-session behavior.
8. Run the complete root-free regression matrix.

The shipped order applied mode `0555` before `isohybrid`. Phase 1D
deliberately moves read-only sealing after final mutation. This preserves the
result while avoiding dependence on root overriding file permissions.

The shipped `mksquashfs` test recognized only versions 4.2 and 4.3. The
current host provides version 4.6.1, so copying that check would incorrectly
disable configured compression. Phase 1D requires one capability-aware
command plan, not the shipped exact-version test and not the current
try-and-repeat fallback.

The current host has `mksquashfs`, `genisoimage`, and `xorriso`. It does not
currently have `isohybrid`; the package dependency is `syslinux-utils`.
Package installation is not part of the root-free implementation lane.

## Phase 1D root-free acceptance

Production commits:

- `2d34aea` — bounded compression planning, legacy finalization, and initial
  characterization;
- `e4746bc` — crash-durable final-image custody and narrow recovery;
- `aa9e1f5` — default real-probe execution and hard-link rejection proof.

The accepted implementation proves:

- unsupported compression is decided by one bounded synthetic probe before
  `mksquashfs` reads the product tree;
- `mksquashfs` executes at most once per rebuild request;
- `isohybrid` runs only for the literal, non-symlink legacy-media profile;
- mutation failure prevents sealing, hashing, sidecar publication, and
  success reporting;
- the ISO is sealed only after all content mutation completes;
- the SHA-256 sidecar records the final ISO bytes using the ISO basename;
- one operation lock spans SquashFS creation through acknowledgement;
- a previous valid ISO and sidecar remain recoverable across every
  publication boundary;
- post-seal recovery resumes hashing or publication without repeating kernel
  work, SquashFS, `genisoimage`, or `isohybrid`;
- altered, foreign, non-regular, hard-linked, path-escaping, corrupt, or
  ownership-mismatched evidence fails closed;
- failures propagate through the current `LiveUSBError` and cleanup-chaining
  boundaries.

Acceptance results:

- focused finalization tests: `26/26`;
- complete GUI-capable suite: `239/239`;
- core-only suite: `238` pass and `1` expected GUI skip;
- syntax and Python 3.8 grammar: `48/48`;
- product and core-only imports: `37/37` and `23/23`;
- process smokes: `4/4`;
- default real compressor outcomes: `xz` accepted and invalid compression
  rejected;
- publication interruption boundaries recovered: `7/7`;
- runtime, journal, pending-journal, candidate, backup, probe, and temporary
  residue: `0`.

Claude Devens independently reconciled oracle cases `16/16` and returned
`ACCEPT`, with `0` blocking and `0` major findings. Two minor test-posture
findings were closed before integration: the real capability probe now runs
by default when `mksquashfs` is available, and a real hard-link case proves
rejection before mutation.

Real root, sudo, mount, unmount, chroot, package, product-ISO, QEMU, Xephyr,
and GUI operations remained `0`. Native Python 3.8 execution, actual power
loss, a complete product ISO build, and the privileged factory cycle remain
untested.

## Real acceptance after Phase 1D

After root-free review and integration:

1. install or otherwise provide the accepted `isohybrid` dependency;
2. use a bounded work directory and the known-good 2015 ubuntuDE ISO;
3. extract with the Python implementation;
4. make customization changes `0`;
5. rebuild and verify the final SHA-256 evidence;
6. boot the output in QEMU;
7. verify input mutations `0`;
8. verify residual mounts, locks, blocked files, journals, staged artifacts,
   and temporary roots are all `0`.

The recovered customization actions remain real product work, but they do
not move this first boot gate.

## Completed team lanes

- Claude Devens produced the independent adversarial oracle and final
  read-only correction review.
- Jacob Codex implemented the bounded lane, crash-durable correction, and
  test-closure micro-lane in an isolated clone.
- George Prime reproduced the compressor-probe defect, integrated only after
  independent acceptance, and executed the production acceptance matrix.

## Next operation

The next bounded operation is dependency and factory preflight. It must
report the missing `isohybrid` dependency, privilege posture, disk space,
legacy-media layout, work-directory custody, and planned commands before any
real extraction or rebuild begins. Package installation and the real
factory cycle require a separately recorded operational gate.

## Review accounting

- Original translator protocol deviations: `1`, limited to remote-tracking
  reference updates; working-tree impact `0`.
- Jacob Codex protocol deviations: `0`.
- Claude Devens protocol deviations: `0`.
- George Prime protocol deviations: `1`, limited to shell-active backticks in
  one documentation search pattern; file, data, test, and persistent impact
  `0`.
- Product paths modified by Phase 1D: `2`.
- Test paths modified by Phase 1D: `2`.
- Historical evidence files modified by this review: `0`.
