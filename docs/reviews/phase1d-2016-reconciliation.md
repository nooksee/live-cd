# Phase 1D: LiveCD Creator 3.13.93 reconciliation

- Date: 2026-07-28
- Status: accepted historical evidence and bounded execution authority
- Product-code changes in this review: `0`
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
| ISO mutation through `isohybrid` | Missing fidelity behavior for the legacy-media path | Phase 1D |
| Final ISO SHA-256 sidecar | Missing fidelity and evidence behavior | Phase 1D |
| Stale SHA-256 sidecar cleanup | Missing lifecycle behavior | Phase 1D |
| `mksquashfs` 4.2/4.3 version test | Confirmed obsolete legacy mechanism | Phase 1D modernization |
| Retry-without-compression after any squash failure | Confirmed unsafe Python divergence because it can repeat a full build for an unrelated failure | Phase 1D correction |
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

The implementation lane must prove:

- stale ISO and SHA-256 outputs are included in bounded cleanup;
- unsupported compression capability is decided before `mksquashfs` runs;
- `mksquashfs` executes at most once per rebuild request;
- `isohybrid` is invoked only for the accepted legacy-media profile;
- `isohybrid` failure prevents read-only sealing, sidecar publication, and
  success reporting;
- the ISO is sealed only after all content mutation completes;
- the SHA-256 sidecar describes the final ISO bytes;
- sidecar-write failure leaves no sidecar that can be mistaken for accepted
  evidence;
- every failure propagates through the current `LiveUSBError` boundary;
- existing transaction and mount-session suites remain green;
- real root, mount, chroot, package, ISO-build, QEMU, and GUI operations
  remain `0`.

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

## Next team lanes

### Claude Devens

Perform a read-only adversarial oracle over the Phase 1D final-image contract.
Challenge command ordering, capability detection, output custody, incomplete
sidecar handling, failure precedence, media-profile boundaries, and
interaction with the accepted transaction layers. Return a test matrix and
any required contract corrections. Modify files `0`.

### Jacob Codex

Implement Phase 1D in an isolated clone from the exact accepted base after
normal custody checks. Restrict writes initially to `liveusb/backend/rebuild.py`
and focused rebuild tests. Run no real privileged or image-building command.
Create one local commit and push `0` branches until George Prime accepts the
reviewed result.

### George Prime

Reconcile both returns, integrate only after acceptance, maintain Git and
dependency custody, and own the later real-operation gate.

## Review accounting

- Original translator protocol deviations: `1`, limited to remote-tracking
  reference updates; working-tree impact `0`.
- George Prime protocol deviations: `0`.
- Product files modified by this review: `0`.
- Historical evidence files modified by this review: `0`.
