"""Root-free factory planning, scoped authorization, and receipt evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from . import extract
from . import mounts
from . import preflight
from . import preflight_runtime
from . import qemu
from . import rebuild
from .. import constants


SCHEMA_VERSION = "liveusb.factory-plan.v1"
RECEIPT_SCHEMA_VERSION = "liveusb.factory-plan-receipt.v1"

OPERATION_EXTRACT = "legacy-extract"
OPERATION_FINALIZE = "legacy-final-image"
OPERATION_QEMU = "bios-qemu"
OPERATIONS = (
    OPERATION_EXTRACT,
    OPERATION_FINALIZE,
    OPERATION_QEMU,
)

DECISION_GRANTED = "granted"
DECISION_REFUSED = "refused"

_GIB = 1024 ** 3
_CAPACITY_FLOOR_BYTES = 32 * _GIB
_CAPACITY_RESERVE_FLOOR_BYTES = 4 * _GIB
_CAPACITY_WORKING_SET_MULTIPLIER = 12
_CAPACITY_SOURCE_RESERVE_MULTIPLIER = 2
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SAFE_ARCHITECTURE = re.compile(r"^(?:amd64|i[3-6]86)$")
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_COMMON_FINDINGS = (
    "workspace.work-root",
    "workspace.confinement",
    "workspace.runtime-root",
    "input.source-iso",
    "capacity.workspace",
    "mount.workspace",
    "lock.operation",
    "journal.mount-session",
    "journal.chroot-transaction",
)

_REQUIRED_TOOLS = {
    OPERATION_EXTRACT: (
        "mount",
        "umount",
        "unsquashfs",
        "rsync",
        "chroot",
    ),
    OPERATION_FINALIZE: (
        "mksquashfs",
        "genisoimage",
        "isohybrid",
        "chroot",
    ),
    OPERATION_QEMU: (
        "qemu-system-x86_64",
    ),
}


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                key: _freeze(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {
            key: _plain(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _sha256_json(value):
    return hashlib.sha256(
        _canonical_json(value).encode("ascii")
    ).hexdigest()


def _path_is_normalized_absolute(path):
    return (
        isinstance(path, str)
        and path
        and "\x00" not in path
        and os.path.isabs(path)
        and os.path.normpath(path) == path
    )


def _path_within(root, path, include_root=False):
    if not _path_is_normalized_absolute(root):
        return False
    if not _path_is_normalized_absolute(path):
        return False
    try:
        common = os.path.commonpath((root, path))
    except ValueError:
        return False
    return common == root and (include_root or path != root)


def _safe_field(value):
    return (
        isinstance(value, str)
        and _SAFE_COMPONENT.fullmatch(value) is not None
    )


def _finding_map(report):
    if not isinstance(report, preflight.PreflightReport):
        raise TypeError("Phase 1E-A report type is invalid")
    if report.schema_version != preflight.SCHEMA_VERSION:
        raise ValueError("Phase 1E-A report schema is invalid")
    return {
        finding.check_id: finding
        for finding in report.findings
    }


def _runtime_map(evidence):
    if not isinstance(evidence, preflight_runtime.RuntimeEvidence):
        raise TypeError("Phase 1E-B1 evidence type is invalid")
    if evidence.schema_version != preflight_runtime.SCHEMA_VERSION:
        raise ValueError("Phase 1E-B1 evidence schema is invalid")
    results = {
        result.probe_id[len("version."):]: result
        for result in evidence.version_queries
        if result.probe_id.startswith("version.")
    }
    version_count = sum(
        result.probe_id.startswith("version.")
        for result in evidence.version_queries
    )
    if len(results) != version_count:
        raise ValueError("Phase 1E-B1 version evidence is duplicated")
    return results


def _required_finding_ids(operation):
    if operation == OPERATION_EXTRACT:
        specific = (
            "workspace.mount-root",
            "workspace.layout",
            "media.source-inspector",
            "publication.prior-pair",
        )
    elif operation == OPERATION_FINALIZE:
        specific = (
            "workspace.filesystem-root",
            "workspace.iso-root",
            "workspace.layout",
            "media.legacy-extracted-profile",
            "publication.prior-pair",
        )
    else:
        specific = (
            "publication.prior-pair",
            "qemu.binary",
        )
    return tuple(dict.fromkeys(_COMMON_FINDINGS + specific))


def _finding_reason(findings, check_id, accepted=(preflight.STATUS_PASS,)):
    finding = findings.get(check_id)
    if finding is None:
        return "finding-missing:" + check_id
    if finding.status not in accepted:
        return "finding-{}:{}".format(check_id, finding.status)
    return None


def _identity_digest(identity):
    if not isinstance(identity, Mapping):
        raise ValueError("Executable identity evidence is invalid")
    return _sha256_json(dict(identity))


@dataclass(frozen=True)
class FactoryBindings:
    """Late-bound paths and media identity needed for one exact plan."""

    mount_destination: Optional[str] = None
    probe_source: Optional[str] = None
    probe_output: Optional[str] = None
    publication_candidate: Optional[str] = None
    distribution_id: Optional[str] = None
    architecture: Optional[str] = None
    release: Optional[str] = None
    compression_supported: Optional[bool] = None

    def to_dict(self):
        return {
            "architecture": self.architecture,
            "compression_supported": self.compression_supported,
            "distribution_id": self.distribution_id,
            "mount_destination": self.mount_destination,
            "probe_output": self.probe_output,
            "probe_source": self.probe_source,
            "publication_candidate": self.publication_candidate,
            "release": self.release,
        }


@dataclass(frozen=True)
class FactoryCommand:
    """One exact external command authorized for one plan stage."""

    stage: str
    tool: str
    argv: Tuple[str, ...]
    cwd: Optional[str] = None

    def __post_init__(self):
        argv = tuple(self.argv)
        if (
            not isinstance(self.stage, str)
            or not self.stage
            or not isinstance(self.tool, str)
            or not self.tool
            or not argv
            or not _path_is_normalized_absolute(argv[0])
            or any(
                not isinstance(value, str) or "\x00" in value
                for value in argv
            )
        ):
            raise ValueError("Factory command contract is invalid")
        if self.cwd is not None and not _path_is_normalized_absolute(
            self.cwd
        ):
            raise ValueError("Factory command working directory is invalid")
        object.__setattr__(self, "argv", argv)

    def to_dict(self):
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "stage": self.stage,
            "tool": self.tool,
        }


@dataclass(frozen=True)
class FactoryReceipt:
    """Minimized stable evidence intended for durable persistence."""

    payload: Mapping[str, Any]

    def __post_init__(self):
        if not isinstance(self.payload, Mapping):
            raise TypeError("Factory receipt payload is invalid")
        object.__setattr__(self, "payload", _freeze(self.payload))

    @property
    def sha256(self):
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def to_dict(self):
        return _plain(self.payload)

    def to_json(self, indent=None):
        return json.dumps(
            _plain(self.payload),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
            ensure_ascii=True,
        )

    def to_bytes(self):
        return (self.to_json() + "\n").encode("ascii")


@dataclass(frozen=True)
class FactoryPlan:
    """One single-use plan and its scoped factory-authority decision."""

    operation: str
    decision: str
    reasons: Tuple[str, ...]
    commands: Tuple[FactoryCommand, ...]
    capacity: Mapping[str, Any]
    evidence_digest: str
    plan_digest: str
    grant_id: Optional[str]
    source_artifact_id: Optional[str]
    tool_evidence: Tuple[Mapping[str, Any], ...]
    receipt: FactoryReceipt
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "capacity", _freeze(self.capacity))
        object.__setattr__(
            self,
            "tool_evidence",
            tuple(_freeze(value) for value in self.tool_evidence),
        )
        if self.operation not in OPERATIONS:
            raise ValueError("Factory operation is invalid")
        if self.decision not in {DECISION_GRANTED, DECISION_REFUSED}:
            raise ValueError("Factory decision is invalid")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Factory plan schema is invalid")
        if _HEX_SHA256.fullmatch(self.evidence_digest) is None:
            raise ValueError("Factory evidence digest is invalid")
        if _HEX_SHA256.fullmatch(self.plan_digest) is None:
            raise ValueError("Factory plan digest is invalid")
        if self.decision == DECISION_GRANTED:
            if self.reasons or not self.commands or self.grant_id is None:
                raise ValueError("Granted factory plan is incomplete")
        elif self.grant_id is not None or self.commands:
            raise ValueError("Refused factory plan carries authority")

    @property
    def factory_authority_granted(self):
        return self.decision == DECISION_GRANTED

    def to_dict(self):
        return {
            "capacity": _plain(self.capacity),
            "commands": [command.to_dict() for command in self.commands],
            "decision": self.decision,
            "evidence_digest": self.evidence_digest,
            "exact_argv_captured": bool(self.commands),
            "factory_authority_granted": self.factory_authority_granted,
            "grant_id": self.grant_id,
            "operation": self.operation,
            "plan_digest": self.plan_digest,
            "reasons": list(self.reasons),
            "schema_version": self.schema_version,
            "source_artifact_id": self.source_artifact_id,
            "state_change_revokes_authority": True,
            "single_use": True,
            "tool_evidence": [
                _plain(value)
                for value in self.tool_evidence
            ],
        }


class FactoryPlanEngine:
    """Build one root-free plan without executing a factory command."""

    def __init__(
        self,
        *,
        statvfs=None,
        stat_reader=None,
        lstat_reader=None,
        access=None,
        preflight_engine=None,
        expected_tool_owner_uid=0,
        exclude_file=constants.EXCLUDE_FILE,
    ):
        self.statvfs = os.statvfs if statvfs is None else statvfs
        self.stat_reader = os.stat if stat_reader is None else stat_reader
        self.lstat_reader = os.lstat if lstat_reader is None else lstat_reader
        self.access = os.access if access is None else access
        self.preflight_engine = (
            preflight.PreflightEngine(
                which=preflight_runtime._default_resolver,
                statvfs=self.statvfs,
            )
            if preflight_engine is None
            else preflight_engine
        )
        self.expected_tool_owner_uid = int(expected_tool_owner_uid)
        self.exclude_file = os.path.abspath(os.fspath(exclude_file))

    def plan(self, ctx, report, runtime_evidence, operation, bindings=None):
        """Return one refused or granted exact plan for *operation*."""

        if operation not in OPERATIONS:
            raise ValueError("Factory operation is not supported")
        if bindings is None:
            bindings = FactoryBindings()
        if not isinstance(bindings, FactoryBindings):
            raise TypeError("Factory bindings are invalid")

        supplied_findings = _finding_map(report)
        runtime = _runtime_map(runtime_evidence)
        reasons = []
        try:
            findings = _finding_map(
                self.preflight_engine.inspect(ctx)
            )
        except Exception as error:
            findings = supplied_findings
            reasons.append(
                "fresh-preflight:" + type(error).__name__
            )
        required_finding_ids = _required_finding_ids(operation)
        for check_id in required_finding_ids:
            supplied = supplied_findings.get(check_id)
            current = findings.get(check_id)
            if supplied is None:
                reasons.append("finding-missing:" + check_id)
            elif current is None:
                reasons.append("fresh-finding-missing:" + check_id)
            elif (
                check_id != "capacity.workspace"
                and supplied.to_dict() != current.to_dict()
            ):
                reasons.append("finding-stale:" + check_id)
        for check_id in _COMMON_FINDINGS:
            reason = _finding_reason(findings, check_id)
            if reason is not None:
                reasons.append(reason)

        reasons.extend(
            self._operation_finding_reasons(
                findings,
                operation,
            )
        )

        source_artifact_id = None
        source_record = None
        try:
            source_record = self._verify_source(
                findings,
                runtime_evidence,
            )
            source_artifact_id = source_record["stable_descriptor"]
        except Exception as error:
            reasons.append(
                "source-custody:" + type(error).__name__
            )

        tool_records = []
        tool_paths = {}
        for tool in _REQUIRED_TOOLS[operation]:
            try:
                record = self._verify_runtime_tool(runtime, tool)
            except Exception as error:
                reasons.append(
                    "tool-{}:{}".format(tool, type(error).__name__)
                )
            else:
                tool_records.append(record)
                tool_paths[tool] = record["path"]

        capacity = self._capacity_decision(
            ctx,
            findings,
            source_record,
            operation,
        )
        if capacity["sufficient"] is not True:
            reasons.append("capacity-insufficient-or-unresolved")

        binding_reasons = self._binding_reasons(
            ctx,
            operation,
            bindings,
        )
        reasons.extend(binding_reasons)

        evidence_projection = {
            "capacity": capacity,
            "findings": {
                check_id: findings[check_id].to_dict()
                for check_id in required_finding_ids
                if check_id in findings
            },
            "operation": operation,
            "source": source_record,
            "tools": [
                {
                    "identity_sha256": record["identity_sha256"],
                    "status": record["status"],
                    "tool": record["tool"],
                    "version_line": record["version_line"],
                }
                for record in sorted(
                    tool_records,
                    key=lambda value: value["tool"],
                )
            ],
        }
        evidence_digest = _sha256_json(evidence_projection)

        reasons = tuple(sorted(set(reasons)))
        commands = ()
        if not reasons:
            try:
                commands = self._commands(
                    ctx,
                    operation,
                    bindings,
                    tool_paths,
                )
            except Exception as error:
                reasons = (
                    "command-construction:" + type(error).__name__,
                )

        plan_projection = {
            "bindings": bindings.to_dict(),
            "capacity": capacity,
            "commands": [command.to_dict() for command in commands],
            "evidence_digest": evidence_digest,
            "operation": operation,
            "reasons": list(reasons),
            "schema_version": SCHEMA_VERSION,
        }
        plan_digest = _sha256_json(plan_projection)
        decision = DECISION_GRANTED if commands else DECISION_REFUSED
        grant_id = (
            hashlib.sha256(
                ("liveusb-factory-grant-v1\x00" + plan_digest).encode(
                    "ascii"
                )
            ).hexdigest()
            if decision == DECISION_GRANTED
            else None
        )
        receipt = self._receipt(
            ctx,
            operation,
            decision,
            reasons,
            commands,
            capacity,
            evidence_digest,
            plan_digest,
            grant_id,
            source_artifact_id,
            tool_records,
            bindings,
        )
        return FactoryPlan(
            operation=operation,
            decision=decision,
            reasons=reasons,
            commands=commands,
            capacity=capacity,
            evidence_digest=evidence_digest,
            plan_digest=plan_digest,
            grant_id=grant_id,
            source_artifact_id=source_artifact_id,
            tool_evidence=tuple(
                {
                    "identity_sha256": record["identity_sha256"],
                    "status": record["status"],
                    "tool": record["tool"],
                    "version_line": record["version_line"],
                }
                for record in sorted(
                    tool_records,
                    key=lambda value: value["tool"],
                )
            ),
            receipt=receipt,
        )

    @staticmethod
    def _operation_finding_reasons(findings, operation):
        reasons = []
        if operation == OPERATION_EXTRACT:
            required = (
                "workspace.mount-root",
                "workspace.layout",
                "media.source-inspector",
                "publication.prior-pair",
            )
            for check_id in required:
                reason = _finding_reason(findings, check_id)
                if reason is not None:
                    reasons.append(reason)
            layout = findings.get("workspace.layout")
            if (
                layout is None
                or layout.evidence.get("state") != "empty"
            ):
                reasons.append("workspace-is-not-empty")
        elif operation == OPERATION_FINALIZE:
            required = (
                "workspace.filesystem-root",
                "workspace.iso-root",
                "workspace.layout",
                "media.legacy-extracted-profile",
                "publication.prior-pair",
            )
            for check_id in required:
                reason = _finding_reason(findings, check_id)
                if reason is not None:
                    reasons.append(reason)
            layout = findings.get("workspace.layout")
            if (
                layout is None
                or layout.evidence.get("state") != "extracted"
            ):
                reasons.append("workspace-is-not-extracted")
        else:
            reason = _finding_reason(
                findings,
                "publication.prior-pair",
            )
            if reason is not None:
                reasons.append(reason)
            publication = findings.get("publication.prior-pair")
            valid_pairs = (
                []
                if publication is None
                else publication.evidence.get("valid_pairs", [])
            )
            if len(valid_pairs) != 1:
                reasons.append("one-valid-publication-pair-is-required")
            reason = _finding_reason(findings, "qemu.binary")
            if reason is not None:
                reasons.append(reason)
        return reasons

    @staticmethod
    def _verify_source(findings, runtime_evidence):
        source_finding = findings.get("input.source-iso")
        identity, digest = preflight_runtime._source_finding_fields(
            source_finding
        )
        descriptor, state, before_digest = preflight_runtime._open_source(
            identity,
            digest,
        )
        try:
            accepted_after, after_digest = (
                preflight_runtime._validate_source_after(
                    descriptor,
                    state,
                    identity,
                    digest,
                )
            )
        finally:
            os.close(descriptor)
        if before_digest != digest or after_digest != digest:
            raise ValueError("Source digest changed during planning")
        media = runtime_evidence.source_media
        if (
            media is None
            or media.status != preflight_runtime.STATUS_SUCCESS
            or media.evidence.get("phase_1e_a_sha256") != digest
            or media.evidence.get("source_sha256_before") != digest
            or media.evidence.get("source_sha256_after") != digest
            or media.evidence.get("termination_confirmed") is not True
            or media.evidence.get("profile", {}).get("recognized") is not True
            or media.evidence.get("profile", {}).get("profile")
            != "legacy-isolinux-single-filesystem-source-media"
        ):
            raise ValueError("Runtime source profile evidence is not accepted")
        return {
            "sha256": digest,
            "size_bytes": identity["size"],
            "stable_descriptor": "sha256:{}:size:{}".format(
                digest,
                identity["size"],
            ),
            "validated_identity": accepted_after == identity,
        }

    def _verify_runtime_tool(self, runtime, tool):
        result = runtime.get(tool)
        if result is None:
            raise ValueError("Runtime tool evidence is absent")
        accepted_nonzero = (
            tool == "unsquashfs"
            and result.status == preflight_runtime.STATUS_NONZERO
            and result.evidence.get("version_output_matched") is True
        )
        if (
            result.status != preflight_runtime.STATUS_SUCCESS
            and not accepted_nonzero
        ):
            raise ValueError("Runtime tool status is not accepted")
        if result.evidence.get("termination_confirmed") is not True:
            raise ValueError("Runtime tool termination is not confirmed")
        if not result.command or not _path_is_normalized_absolute(
            result.command[0]
        ):
            raise ValueError("Runtime tool command is not exact")
        path = result.command[0]
        before = result.evidence.get("executable_identity_before")
        after = result.evidence.get("executable_identity_after")
        if not isinstance(before, Mapping) or dict(before) != dict(after or {}):
            raise ValueError("Runtime tool identity is inconsistent")
        literal_state = self.lstat_reader(path)
        state = self.stat_reader(path)
        current = preflight_runtime._node_identity(state)
        if dict(before) != current:
            raise ValueError("Runtime tool identity is stale")
        if (
            not stat.S_ISREG(literal_state.st_mode)
            or stat.S_ISLNK(literal_state.st_mode)
            or preflight_runtime._node_identity(literal_state) != current
            or not stat.S_ISREG(state.st_mode)
            or state.st_nlink != 1
            or state.st_uid != self.expected_tool_owner_uid
            or stat.S_IMODE(state.st_mode) & 0o022
            or not self.access(path, os.X_OK)
        ):
            raise ValueError("Runtime tool custody is untrusted")
        version_line = result.evidence.get("version_line")
        if not isinstance(version_line, str) or not version_line:
            raise ValueError("Runtime tool version evidence is absent")
        return {
            "identity_sha256": _identity_digest(current),
            "path": path,
            "status": result.status,
            "tool": tool,
            "version_line": version_line,
        }

    def _capacity_decision(self, ctx, findings, source_record, operation):
        capacity_finding = findings.get("capacity.workspace")
        observed = (
            None
            if capacity_finding is None
            else capacity_finding.evidence.get("available_bytes")
        )
        source_size = (
            None
            if source_record is None
            else source_record.get("size_bytes")
        )
        try:
            value = self.statvfs(ctx.work_dir)
            fragment = int(value.f_frsize or value.f_bsize)
            current = int(value.f_bavail) * fragment
        except Exception:
            current = None
        if operation == OPERATION_QEMU:
            requirement = 0
            reserve = 0
        elif type(source_size) is int and source_size >= 0:
            reserve = max(
                _CAPACITY_RESERVE_FLOOR_BYTES,
                source_size * _CAPACITY_SOURCE_RESERVE_MULTIPLIER,
            )
            requirement = max(
                _CAPACITY_FLOOR_BYTES,
                source_size * _CAPACITY_WORKING_SET_MULTIPLIER
                + reserve,
            )
        else:
            reserve = None
            requirement = None
        available_values = [
            value
            for value in (observed, current)
            if type(value) is int and value >= 0
        ]
        effective = min(available_values) if len(available_values) == 2 else None
        sufficient = (
            effective >= requirement
            if effective is not None and requirement is not None
            else None
        )
        return {
            "available_bytes_current": current,
            "available_bytes_phase_1e_a": observed,
            "effective_available_bytes": effective,
            "formula": (
                "max(32GiB,source_size*12+max(4GiB,source_size*2))"
                if operation != OPERATION_QEMU
                else "0"
            ),
            "mathematical_upper_bound": False,
            "requirement_bytes": requirement,
            "safety_reserve_bytes": reserve,
            "source_size_bytes": source_size,
            "sufficient": sufficient,
        }

    def _binding_reasons(self, ctx, operation, bindings):
        reasons = []
        if operation == OPERATION_EXTRACT:
            destination = bindings.mount_destination
            if (
                not _path_within(
                    os.path.abspath(ctx.mount_dir),
                    destination,
                )
                or not os.path.basename(destination).startswith(
                    "liveusb-iso-"
                )
            ):
                reasons.append("mount-destination-binding-is-invalid")
            elif os.path.lexists(destination):
                reasons.append("mount-destination-already-exists")
        elif operation == OPERATION_FINALIZE:
            for name in (
                "probe_source",
                "probe_output",
                "publication_candidate",
            ):
                value = getattr(bindings, name)
                if not _path_within(os.path.abspath(ctx.work_dir), value):
                    reasons.append(name.replace("_", "-") + "-binding-is-invalid")
            if (
                _path_is_normalized_absolute(bindings.probe_source)
                and not os.path.basename(
                    os.path.dirname(bindings.probe_source)
                ).startswith(".liveusb-compression-probe-")
            ):
                reasons.append("probe-source-namespace-is-invalid")
            probe_root = (
                os.path.dirname(bindings.probe_source)
                if _path_is_normalized_absolute(bindings.probe_source)
                else None
            )
            if (
                probe_root is not None
                and (
                    os.path.dirname(probe_root)
                    != os.path.abspath(ctx.work_dir)
                    or os.path.lexists(probe_root)
                )
            ):
                reasons.append("probe-root-custody-is-invalid")
            if (
                _path_is_normalized_absolute(bindings.probe_output)
                and os.path.dirname(bindings.probe_output)
                != os.path.dirname(bindings.probe_source or "")
            ):
                reasons.append("probe-bindings-do-not-share-custody")
            if (
                _path_is_normalized_absolute(bindings.publication_candidate)
                and (
                    os.path.dirname(bindings.publication_candidate)
                    != os.path.abspath(ctx.work_dir)
                    or not os.path.basename(
                        bindings.publication_candidate
                    ).startswith(".liveusb-publish-")
                )
            ):
                reasons.append("publication-namespace-is-invalid")
            for value in (
                bindings.probe_source,
                bindings.probe_output,
                bindings.publication_candidate,
            ):
                if _path_is_normalized_absolute(value) and os.path.lexists(value):
                    reasons.append("bound-output-already-exists")
            if bindings.compression_supported is not True and (
                bindings.compression_supported is not False
            ):
                reasons.append("compression-capability-is-unresolved")
            if not _safe_field(bindings.distribution_id):
                reasons.append("distribution-id-binding-is-invalid")
            if (
                not isinstance(bindings.architecture, str)
                or _SAFE_ARCHITECTURE.fullmatch(bindings.architecture) is None
            ):
                reasons.append("architecture-binding-is-invalid")
            if not _safe_field(bindings.release):
                reasons.append("release-binding-is-invalid")
            if (
                _safe_field(bindings.distribution_id)
                and isinstance(bindings.architecture, str)
                and _SAFE_ARCHITECTURE.fullmatch(bindings.architecture)
                and _safe_field(bindings.release)
                and len(
                    "{}-{}-{}".format(
                        bindings.distribution_id,
                        bindings.architecture,
                        bindings.release,
                    )
                ) > 32
            ):
                reasons.append("iso-volume-label-exceeds-32-characters")
            if not _path_is_normalized_absolute(self.exclude_file):
                reasons.append("exclude-file-path-is-invalid")
        return reasons

    def _commands(self, ctx, operation, bindings, tools):
        if operation == OPERATION_EXTRACT:
            destination = bindings.mount_destination
            request = mounts.iso_mount_request(ctx, destination)
            return (
                FactoryCommand(
                    "source-mount",
                    "mount",
                    mounts.mount_command(
                        request,
                        executable=tools["mount"],
                    ),
                ),
                FactoryCommand(
                    "filesystem-extraction",
                    "unsquashfs",
                    extract.unsquashfs_command(
                        ctx,
                        destination,
                        executable=tools["unsquashfs"],
                    ),
                ),
                FactoryCommand(
                    "target-architecture-observation",
                    "chroot",
                    extract.target_architecture_command(
                        ctx,
                        executable=tools["chroot"],
                    ),
                ),
                FactoryCommand(
                    "media-tree-copy",
                    "rsync",
                    extract.media_tree_copy_command(
                        ctx,
                        destination,
                        executable=tools["rsync"],
                    ),
                ),
                FactoryCommand(
                    "source-unmount",
                    "umount",
                    mounts.unmount_command(
                        destination,
                        executable=tools["umount"],
                        lazy=False,
                    ),
                ),
            )
        if operation == OPERATION_FINALIZE:
            label = "{}-{}-{}".format(
                bindings.distribution_id,
                bindings.architecture,
                bindings.release,
            )
            squashfs_output = os.path.join(
                os.path.abspath(ctx.iso_dir),
                "casper",
                "filesystem.squashfs",
            )
            squashfs_argv = rebuild.mksquashfs_command(
                ctx,
                squashfs_output,
                bindings.compression_supported,
                executable=tools["mksquashfs"],
                exclude_file=self.exclude_file,
            )
            return (
                FactoryCommand(
                    "squashfs-capability-probe",
                    "mksquashfs",
                    rebuild.compression_probe_command(
                        ctx.compression,
                        bindings.probe_source,
                        bindings.probe_output,
                        executable=tools["mksquashfs"],
                    ),
                ),
                FactoryCommand(
                    "squashfs-build",
                    "mksquashfs",
                    squashfs_argv,
                ),
                FactoryCommand(
                    "manifest-query",
                    "chroot",
                    rebuild.manifest_query_command(
                        ctx,
                        executable=tools["chroot"],
                    ),
                ),
                FactoryCommand(
                    "iso-generation",
                    "genisoimage",
                    rebuild.genisoimage_command(
                        label,
                        bindings.publication_candidate,
                        executable=tools["genisoimage"],
                    ),
                    cwd=os.path.abspath(ctx.iso_dir),
                ),
                FactoryCommand(
                    "legacy-isohybrid-mutation",
                    "isohybrid",
                    rebuild.isohybrid_command(
                        bindings.publication_candidate,
                        executable=tools["isohybrid"],
                    ),
                ),
            )
        publication = self._publication_pair(ctx)
        return (
            FactoryCommand(
                "bios-qemu",
                "qemu-system-x86_64",
                qemu.qemu_command(
                    tools["qemu-system-x86_64"],
                    publication,
                    ctx.vram,
                ),
            ),
        )

    @staticmethod
    def _publication_pair(ctx):
        candidates = sorted(
            path
            for path in (
                os.path.join(ctx.work_dir, name)
                for name in os.listdir(ctx.work_dir)
            )
            if path.endswith(".iso")
            and os.path.exists(path[:-4] + ".sha256")
            and os.path.abspath(path) != os.path.abspath(ctx.iso)
        )
        if len(candidates) != 1:
            raise ValueError("One final publication pair is required")
        rebuild._validate_prior_pair(
            candidates[0],
            candidates[0][:-4] + ".sha256",
        )
        return os.path.abspath(candidates[0])

    def _receipt(
        self,
        ctx,
        operation,
        decision,
        reasons,
        commands,
        capacity,
        evidence_digest,
        plan_digest,
        grant_id,
        source_artifact_id,
        tool_records,
        bindings,
    ):
        replacements = {
            os.path.abspath(ctx.work_dir): "${WORK_DIR}",
            os.path.abspath(ctx.fs_dir): "${FILESYSTEM_ROOT}",
            os.path.abspath(ctx.iso_dir): "${ISO_ROOT}",
            os.path.abspath(ctx.mount_dir): "${MOUNT_ROOT}",
            os.path.abspath(ctx.iso): "${SOURCE_ISO}",
        }
        binding_tokens = {
            bindings.mount_destination: "${ISO_MOUNT}",
            bindings.probe_source: "${PROBE_SOURCE}",
            bindings.probe_output: "${PROBE_OUTPUT}",
            bindings.publication_candidate: "${PUBLICATION_CANDIDATE}",
        }
        for path, token in binding_tokens.items():
            if _path_is_normalized_absolute(path):
                replacements[path] = token
        for record in tool_records:
            replacements[record["path"]] = "${TOOL:" + record["tool"] + "}"

        def symbolic(value):
            if value is None:
                return None
            result = value
            for path, token in sorted(
                replacements.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                if result == path:
                    return token
                if result.startswith(path + os.sep):
                    return token + result[len(path):]
            return result

        minimized_commands = [
            {
                "argv": [symbolic(value) for value in command.argv],
                "cwd": symbolic(command.cwd),
                "stage": command.stage,
                "tool": command.tool,
            }
            for command in commands
        ]
        payload = {
            "capacity": {
                "effective_available_bytes": capacity[
                    "effective_available_bytes"
                ],
                "formula": capacity["formula"],
                "mathematical_upper_bound": capacity[
                    "mathematical_upper_bound"
                ],
                "requirement_bytes": capacity["requirement_bytes"],
                "sufficient": capacity["sufficient"],
            },
            "commands": minimized_commands,
            "commands_executed": 0,
            "decision": decision,
            "evidence_digest": evidence_digest,
            "exact_argv_captured": bool(commands),
            "factory_authority_granted": decision == DECISION_GRANTED,
            "grant_id": grant_id,
            "operation": operation,
            "phase": "1E-B2A",
            "plan_digest": plan_digest,
            "privileged_operations_executed": 0,
            "reasons": list(reasons),
            "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
            "source_artifact_id": source_artifact_id,
            "state_change_revokes_authority": True,
            "single_use": True,
            "tool_evidence": [
                {
                    "identity_sha256": record["identity_sha256"],
                    "status": record["status"],
                    "tool": record["tool"],
                    "version_line": record["version_line"],
                }
                for record in sorted(
                    tool_records,
                    key=lambda value: value["tool"],
                )
            ],
        }
        return FactoryReceipt(payload)


def write_receipt(path, receipt, expected_owner_uid=None):
    """Persist one complete receipt atomically without replacing evidence."""

    if not isinstance(receipt, FactoryReceipt):
        raise TypeError("Factory receipt type is invalid")
    target = os.path.abspath(os.fspath(path))
    if not _path_is_normalized_absolute(target):
        raise ValueError("Factory receipt path is invalid")
    parent = os.path.dirname(target)
    target_name = os.path.basename(target)
    if target_name in {"", ".", ".."} or os.sep in target_name:
        raise ValueError("Factory receipt filename is invalid")
    parent_state = os.lstat(parent)
    owner_uid = os.geteuid() if expected_owner_uid is None else int(
        expected_owner_uid
    )
    if (
        os.path.realpath(parent) != parent
        or not stat.S_ISDIR(parent_state.st_mode)
        or stat.S_ISLNK(parent_state.st_mode)
        or parent_state.st_uid != owner_uid
        or stat.S_IMODE(parent_state.st_mode) & 0o022
    ):
        raise ValueError("Factory receipt directory custody is invalid")
    pending_name = ".{}.pending-{}".format(
        target_name,
        uuid.uuid4().hex,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_descriptor = os.open(parent, directory_flags)
    descriptor = None
    linked = False
    published_identity = None
    try:
        opened_parent_state = os.fstat(directory_descriptor)
        if (
            opened_parent_state.st_dev != parent_state.st_dev
            or opened_parent_state.st_ino != parent_state.st_ino
            or not stat.S_ISDIR(opened_parent_state.st_mode)
            or opened_parent_state.st_uid != parent_state.st_uid
            or stat.S_IMODE(opened_parent_state.st_mode)
            != stat.S_IMODE(parent_state.st_mode)
        ):
            raise ValueError("Factory receipt directory changed")
        try:
            os.stat(
                target_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(target)

        descriptor = os.open(
            pending_name,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        payload = receipt.to_bytes()
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("Factory receipt write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        current_parent_state = os.lstat(parent)
        if (
            current_parent_state.st_dev != parent_state.st_dev
            or current_parent_state.st_ino != parent_state.st_ino
            or current_parent_state.st_uid != parent_state.st_uid
            or stat.S_IMODE(current_parent_state.st_mode)
            != stat.S_IMODE(parent_state.st_mode)
            or os.path.realpath(parent) != parent
        ):
            raise ValueError("Factory receipt directory changed")
        pending_state = os.stat(
            pending_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        published_identity = (
            pending_state.st_dev,
            pending_state.st_ino,
        )
        os.link(
            pending_name,
            target_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        linked = True
        published = os.stat(
            target_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (published.st_dev, published.st_ino) != published_identity:
            raise ValueError("Factory receipt publication identity changed")
        os.fsync(directory_descriptor)
        os.unlink(pending_name, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
        final_state = os.stat(
            target_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        final_parent_state = os.lstat(parent)
        if (
            (final_state.st_dev, final_state.st_ino)
            != published_identity
            or not stat.S_ISREG(final_state.st_mode)
            or stat.S_ISLNK(final_state.st_mode)
            or final_state.st_nlink != 1
            or stat.S_IMODE(final_state.st_mode) != 0o600
            or final_parent_state.st_dev != parent_state.st_dev
            or final_parent_state.st_ino != parent_state.st_ino
            or final_parent_state.st_uid != parent_state.st_uid
            or stat.S_IMODE(final_parent_state.st_mode)
            != stat.S_IMODE(parent_state.st_mode)
            or os.path.realpath(parent) != parent
        ):
            raise ValueError("Factory receipt publication changed")
    except Exception:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if linked and published_identity is not None:
            try:
                current = os.stat(
                    target_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == published_identity:
                    os.unlink(target_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        try:
            os.unlink(pending_name, dir_fd=directory_descriptor)
        except OSError:
            pass
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
        raise
    finally:
        try:
            os.close(directory_descriptor)
        except OSError:
            pass
    return receipt.sha256
