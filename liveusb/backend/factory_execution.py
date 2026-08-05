"""Phase 1E-B2B complete-rebuild authorization and execution custody."""

from __future__ import annotations

import copy
import fcntl
import glob
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from . import factory_plan
from . import mount_session
from . import mounts
from . import preflight
from . import preflight_runtime
from . import rebuild
from . import run as backend_run
from .. import messages


SCHEMA_VERSION = "liveusb.factory-execution-plan.v1"
GRANT_SCHEMA_VERSION = "liveusb.factory-execution-grant.v1"
STATE_SCHEMA_VERSION = "liveusb.factory-execution-state.v1"
OUTCOME_SCHEMA_VERSION = "liveusb.factory-execution-outcome.v1"

OPERATION_COMPLETE_REBUILD = "legacy-complete-rebuild"

DECISION_GRANTED = "granted"
DECISION_REFUSED = "refused"

STATE_ISSUED = "issued"
STATE_CONSUMED = "consumed"
STATE_REVOKED = "revoked"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_INTERRUPTED = "interrupted"
TERMINAL_STATES = (
    STATE_REVOKED,
    STATE_SUCCEEDED,
    STATE_FAILED,
    STATE_INTERRUPTED,
)

_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FIELD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SAFE_ARCHITECTURE = re.compile(r"^(?:amd64|i[3-6]86)$")
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_GLOBAL_LOCK_NAME = "factory-execution.lock"
_GRANT_PREFIX = "grant-"


class FactoryExecutionError(messages.LiveUSBError):
    """Fail-closed error for factory authorization or execution."""


def _canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sha256_json(value):
    return hashlib.sha256(
        _canonical_json(value).encode("ascii")
    ).hexdigest()


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                key: _freeze(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (tuple, list)):
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


def _read_exact_file(path, maximum=_MAX_METADATA_BYTES):
    path = os.path.abspath(os.fspath(path))
    literal = os.lstat(path)
    if (
        not stat.S_ISREG(literal.st_mode)
        or stat.S_ISLNK(literal.st_mode)
        or literal.st_nlink != 1
        or literal.st_size > maximum
    ):
        raise ValueError("Factory metadata file custody is invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != literal.st_dev
            or opened.st_ino != literal.st_ino
            or opened.st_size != literal.st_size
        ):
            raise ValueError("Factory metadata identity changed")
        chunks = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                break
        final = os.fstat(descriptor)
        path_state = os.lstat(path)
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or final.st_size != opened.st_size
            or path_state.st_dev != opened.st_dev
            or path_state.st_ino != opened.st_ino
            or path_state.st_size != opened.st_size
        ):
            raise ValueError("Factory metadata changed while reading")
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > maximum:
        raise ValueError("Factory metadata exceeds the size limit")
    return raw, {
        "mode": stat.S_IMODE(literal.st_mode),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": literal.st_size,
    }


def _hash_exact_file(path):
    path = os.path.abspath(os.fspath(path))
    literal = os.lstat(path)
    if (
        not stat.S_ISREG(literal.st_mode)
        or stat.S_ISLNK(literal.st_mode)
        or literal.st_nlink != 1
    ):
        raise ValueError("Factory artifact custody is invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != literal.st_dev
            or opened.st_ino != literal.st_ino
            or opened.st_size != literal.st_size
        ):
            raise ValueError("Factory artifact identity changed")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        final = os.fstat(descriptor)
        path_state = os.lstat(path)
        if (
            final.st_dev != opened.st_dev
            or final.st_ino != opened.st_ino
            or final.st_size != opened.st_size
            or path_state.st_dev != opened.st_dev
            or path_state.st_ino != opened.st_ino
            or path_state.st_size != opened.st_size
        ):
            raise ValueError("Factory artifact changed while hashing")
    finally:
        os.close(descriptor)
    return {
        "mode": stat.S_IMODE(literal.st_mode),
        "sha256": digest.hexdigest(),
        "size": literal.st_size,
    }


def _parse_key_value(raw, required):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Factory metadata is not UTF-8") from error
    values = {}
    for line in text.splitlines():
        for key in required:
            marker = key + "="
            if line.startswith(marker):
                value = line[len(marker):].strip().strip('"')
                if key in values and values[key] != value:
                    raise ValueError("Factory metadata key is duplicated")
                values[key] = value
    if set(values) != set(required):
        raise ValueError("Factory metadata keys are incomplete")
    return values


def _symbolic_path(
    ctx,
    value,
    tool_paths=None,
    bindings=None,
    extra_paths=None,
):
    if value is None:
        return None
    replacements = {
        os.path.abspath(ctx.work_dir): "${WORK_DIR}",
        os.path.abspath(ctx.fs_dir): "${FILESYSTEM_ROOT}",
        os.path.abspath(ctx.iso_dir): "${ISO_ROOT}",
        os.path.abspath(ctx.mount_dir): "${MOUNT_ROOT}",
        os.path.abspath(ctx.iso): "${SOURCE_ISO}",
    }
    for tool, path in (tool_paths or {}).items():
        replacements[path] = "${TOOL:" + tool + "}"
    for path, token in (extra_paths or {}).items():
        replacements[path] = token
    if bindings is not None:
        bound = {
            bindings.probe_source: "${PROBE_SOURCE}",
            bindings.probe_output: "${PROBE_OUTPUT}",
            bindings.publication_candidate: "${PUBLICATION_CANDIDATE}",
        }
        for path, token in bound.items():
            if _path_is_normalized_absolute(path):
                replacements[path] = token
    for path, token in sorted(
        replacements.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if value == path:
            return token
        if value.startswith(path + os.sep):
            return token + value[len(path):]
    return value


@dataclass(frozen=True)
class CompleteRebuildAuthorization:
    """One immutable complete-rebuild decision and exact command surface."""

    decision: str
    reasons: Tuple[str, ...]
    session_token: str
    publication_nonce: str
    bindings: factory_plan.FactoryBindings
    b2a_plan: factory_plan.FactoryPlan
    tool_paths: Mapping[str, str]
    support_tools: Mapping[str, Any]
    metadata: Mapping[str, Any]
    kernel: Mapping[str, Any]
    mutation_authority: Tuple[Mapping[str, Any], ...]
    exact_commands: Tuple[Mapping[str, Any], ...]
    symbolic_commands: Tuple[Mapping[str, Any], ...]
    lifecycle_digest: str
    grant_id: Optional[str]
    receipt: factory_plan.FactoryReceipt
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        object.__setattr__(self, "reasons", tuple(self.reasons))
        object.__setattr__(self, "tool_paths", _freeze(self.tool_paths))
        object.__setattr__(
            self,
            "support_tools",
            _freeze(self.support_tools),
        )
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        object.__setattr__(self, "kernel", _freeze(self.kernel))
        object.__setattr__(
            self,
            "mutation_authority",
            tuple(_freeze(item) for item in self.mutation_authority),
        )
        object.__setattr__(
            self,
            "exact_commands",
            tuple(_freeze(item) for item in self.exact_commands),
        )
        object.__setattr__(
            self,
            "symbolic_commands",
            tuple(_freeze(item) for item in self.symbolic_commands),
        )
        if self.decision not in {DECISION_GRANTED, DECISION_REFUSED}:
            raise ValueError("Complete rebuild decision is invalid")
        if _HEX_32.fullmatch(self.session_token) is None:
            raise ValueError("Complete rebuild session token is invalid")
        if _HEX_32.fullmatch(self.publication_nonce) is None:
            raise ValueError("Complete rebuild publication nonce is invalid")
        if _HEX_64.fullmatch(self.lifecycle_digest) is None:
            raise ValueError("Complete rebuild lifecycle digest is invalid")
        if self.decision == DECISION_GRANTED:
            if self.reasons or self.grant_id is None:
                raise ValueError("Granted complete rebuild is incomplete")
        elif self.grant_id is not None:
            raise ValueError("Refused complete rebuild carries a grant")

    @property
    def factory_authority_granted(self):
        return self.decision == DECISION_GRANTED

    def grant_payload(self):
        payload = {
            "b2a_evidence_digest": self.b2a_plan.evidence_digest,
            "b2a_grant_id": self.b2a_plan.grant_id,
            "b2a_plan_digest": self.b2a_plan.plan_digest,
            "decision": self.decision,
            "exact_commands": [
                _plain(item)
                for item in self.symbolic_commands
            ],
            "factory_authority_granted": self.factory_authority_granted,
            "grant_id": self.grant_id,
            "kernel": _plain(self.kernel),
            "lifecycle_digest": self.lifecycle_digest,
            "metadata": _plain(self.metadata),
            "mutation_authority": [
                _plain(item)
                for item in self.mutation_authority
            ],
            "operation": OPERATION_COMPLETE_REBUILD,
            "publication_nonce": self.publication_nonce,
            "reasons": list(self.reasons),
            "schema_version": GRANT_SCHEMA_VERSION,
            "session_token": self.session_token,
            "source_artifact_id": self.b2a_plan.source_artifact_id,
            "state_change_revokes_authority": True,
            "single_use": True,
            "tool_evidence": [
                _plain(item)
                for item in self.b2a_plan.tool_evidence
            ],
            "transaction_support_tool_evidence": _plain(
                self.support_tools
            ),
        }
        payload["document_sha256"] = _sha256_json(payload)
        return payload


class FactoryExecutionEngine:
    """Collect fresh evidence and authorize one complete rebuild."""

    def __init__(
        self,
        *,
        preflight_engine=None,
        runtime_engine=None,
        plan_engine=None,
        compression_probe=None,
        token_factory=None,
        runner=None,
        expected_tool_owner_uid=0,
    ):
        self.preflight_engine = (
            preflight.PreflightEngine(
                which=preflight_runtime._default_resolver,
            )
            if preflight_engine is None
            else preflight_engine
        )
        self.runtime_engine = (
            preflight_runtime.RuntimeEvidenceEngine()
            if runtime_engine is None
            else runtime_engine
        )
        self.plan_engine = (
            factory_plan.FactoryPlanEngine(
                preflight_engine=self.preflight_engine,
                expected_tool_owner_uid=expected_tool_owner_uid,
            )
            if plan_engine is None
            else plan_engine
        )
        self.compression_probe = (
            rebuild._compression_is_supported
            if compression_probe is None
            else compression_probe
        )
        self.token_factory = uuid.uuid4 if token_factory is None else token_factory
        self.runner = backend_run if runner is None else runner

    def _token(self):
        value = self.token_factory()
        token = value.hex if hasattr(value, "hex") else str(value)
        if _HEX_32.fullmatch(token) is None:
            raise ValueError("Factory token source returned an invalid token")
        return token

    @staticmethod
    def _source_finding(report):
        for finding in report.findings:
            if finding.check_id == "input.source-iso":
                return finding
        raise ValueError("Phase 1E-A source finding is absent")

    @staticmethod
    def _target_metadata(ctx):
        lsb_path = os.path.join(ctx.fs_dir, "etc", "lsb-release")
        arch_path = os.path.join(
            ctx.fs_dir,
            "var",
            "lib",
            "dpkg",
            "arch",
        )
        lsb_raw, lsb_identity = _read_exact_file(lsb_path)
        arch_raw, arch_identity = _read_exact_file(arch_path, 4096)
        values = _parse_key_value(
            lsb_raw,
            ("DISTRIB_ID", "DISTRIB_RELEASE", "DISTRIB_CODENAME"),
        )
        try:
            architecture_lines = [
                line.strip()
                for line in arch_raw.decode("ascii").splitlines()
                if line.strip()
            ]
        except UnicodeDecodeError as error:
            raise ValueError("Target architecture metadata is not ASCII") from error
        if (
            not architecture_lines
            or _SAFE_ARCHITECTURE.fullmatch(architecture_lines[0]) is None
            or not _SAFE_FIELD.fullmatch(values["DISTRIB_ID"])
            or not _SAFE_FIELD.fullmatch(values["DISTRIB_RELEASE"])
            or not _SAFE_FIELD.fullmatch(values["DISTRIB_CODENAME"])
        ):
            raise ValueError("Target distribution metadata is invalid")
        return {
            "architecture": architecture_lines[0],
            "architecture_file_sha256": arch_identity["sha256"],
            "codename": values["DISTRIB_CODENAME"],
            "distribution_id": values["DISTRIB_ID"],
            "lsb_release_sha256": lsb_identity["sha256"],
            "release": values["DISTRIB_RELEASE"],
        }

    @staticmethod
    def _kernel_state(ctx):
        boot_root = os.path.join(ctx.fs_dir, "boot")
        initrd = sorted(glob.glob(os.path.join(boot_root, "initrd.img-*")))
        vmlinuz = sorted(glob.glob(os.path.join(boot_root, "vmlinuz-*")))
        selected_initrd = initrd[-1] if initrd else None
        selected_vmlinuz = vmlinuz[-1] if vmlinuz else None
        sources = []
        for label, path in (
            ("initrd", selected_initrd),
            ("vmlinuz", selected_vmlinuz),
        ):
            if path is None:
                continue
            identity = _hash_exact_file(path)
            sources.append(
                {
                    "kind": label,
                    "relative_path": os.path.relpath(path, boot_root),
                    "sha256": identity["sha256"],
                    "size": identity["size"],
                }
            )
        mode = (
            "update-initramfs"
            if selected_initrd is not None and selected_vmlinuz is not None
            else "install-kernel"
        )
        return {
            "mode": mode,
            "sources": sources,
        }

    @staticmethod
    def _chroot_prefix(ctx, chroot_path):
        return (
            chroot_path,
            ctx.fs_dir,
            "env",
            "HOME=/root",
            "LC_ALL=" + ctx.locales,
            "LANGUAGE=" + ctx.locales,
            "LANG=" + ctx.locales,
        )

    def _additional_tool(self, runtime_evidence, tool):
        runtime = factory_plan._runtime_map(runtime_evidence)
        return self.plan_engine._verify_runtime_tool(runtime, tool)

    def plan_complete_rebuild(
        self,
        ctx,
        *,
        session_token=None,
        publication_nonce=None,
    ):
        session_token = self._token() if session_token is None else session_token
        publication_nonce = (
            self._token()
            if publication_nonce is None
            else publication_nonce
        )
        if _HEX_32.fullmatch(session_token or "") is None:
            raise ValueError("Complete rebuild session token is invalid")
        if _HEX_32.fullmatch(publication_nonce or "") is None:
            raise ValueError("Complete rebuild publication nonce is invalid")

        reasons = []
        metadata = {}
        kernel = {}
        report = self.preflight_engine.inspect(ctx)
        runtime_evidence = self.runtime_engine.collect(
            self._source_finding(report)
        )
        try:
            metadata = self._target_metadata(ctx)
        except Exception as error:
            reasons.append("target-metadata:" + type(error).__name__)
        try:
            kernel = self._kernel_state(ctx)
        except Exception as error:
            reasons.append("kernel-state:" + type(error).__name__)
        try:
            compression_supported = bool(
                self.compression_probe(
                    ctx.compression,
                    scratch_root=ctx.work_dir,
                )
            )
        except Exception as error:
            compression_supported = None
            reasons.append("compression-probe:" + type(error).__name__)

        probe_root = os.path.join(
            os.path.abspath(ctx.work_dir),
            ".liveusb-compression-probe-" + session_token,
        )
        publication_candidate = os.path.join(
            os.path.abspath(ctx.work_dir),
            ".liveusb-publish-{}-{}-primary.candidate".format(
                session_token,
                publication_nonce,
            ),
        )
        bindings = factory_plan.FactoryBindings(
            probe_source=os.path.join(probe_root, "empty-source"),
            probe_output=os.path.join(probe_root, "probe.squashfs"),
            publication_candidate=publication_candidate,
            distribution_id=metadata.get("distribution_id"),
            architecture=metadata.get("architecture"),
            release=metadata.get("release"),
            compression_supported=compression_supported,
        )
        b2a_plan = self.plan_engine.plan(
            ctx,
            report,
            runtime_evidence,
            factory_plan.OPERATION_FINALIZE,
            bindings,
        )
        reasons.extend(b2a_plan.reasons)

        tool_records = {}
        for tool in ("mount", "umount"):
            try:
                tool_records[tool] = self._additional_tool(
                    runtime_evidence,
                    tool,
                )
            except Exception as error:
                reasons.append(
                    "tool-{}:{}".format(tool, type(error).__name__)
                )
        runtime = factory_plan._runtime_map(runtime_evidence)
        tool_paths = {}
        for tool in (
            "mksquashfs",
            "genisoimage",
            "isohybrid",
            "chroot",
        ):
            try:
                tool_paths[tool] = self.plan_engine._verify_runtime_tool(
                    runtime,
                    tool,
                )["path"]
            except Exception:
                pass
        for tool, record in tool_records.items():
            tool_paths[tool] = record["path"]
        support_tools = {
            tool: {
                "identity_sha256": record["identity_sha256"],
                "status": record["status"],
                "version_line": record["version_line"],
            }
            for tool, record in sorted(tool_records.items())
        }

        exact_commands = []
        if not reasons:
            architecture_command = rebuild.target_architecture_command(
                ctx,
                executable=tool_paths["chroot"],
            )
            exact_commands.append(
                {
                    "argv": tuple(architecture_command),
                    "authority": "exact",
                    "stage": "target-architecture-observation",
                }
            )
            for index, request in enumerate(mounts.system_mount_requests(ctx)):
                exact_commands.append(
                    {
                        "argv": mounts.mount_command(
                            request,
                            executable=tool_paths["mount"],
                        ),
                        "authority": "exact",
                        "stage": "system-mount-{}".format(index + 1),
                    }
                )
            prefix = self._chroot_prefix(ctx, tool_paths["chroot"])
            targets = (
                (
                    "apt-get",
                    "purge",
                    "--yes",
                    "linux-image*",
                    "linux-headers*",
                    "-qq",
                ),
                (
                    "apt-get",
                    "install",
                    "--yes",
                    "linux-image-generic",
                    "linux-headers-generic",
                    "-qq",
                ),
            ) if kernel["mode"] == "install-kernel" else (
                (
                    "update-initramfs",
                    "-k",
                    "all",
                    "-t",
                    "-u",
                ),
            )
            for index, target in enumerate(targets):
                exact_commands.append(
                    {
                        "argv": prefix + target,
                        "authority": "exact",
                        "stage": "kernel-target-{}".format(index + 1),
                    }
                )
            for command in b2a_plan.commands:
                exact_commands.append(
                    {
                        "argv": command.argv,
                        "authority": "exact",
                        "cwd": command.cwd,
                        "stage": command.stage,
                    }
                )
            exact_commands.extend(
                (
                    {
                        "authority": "mount-session-journal",
                        "stage": "identity-derived-unmounts",
                    },
                    {
                        "authority": "chroot-transaction-journal",
                        "stage": "service-block-and-cleanup",
                    },
                )
            )

        symbolic_commands = []
        for command in exact_commands:
            symbolic = dict(command)
            if "argv" in symbolic:
                symbolic["argv"] = [
                    _symbolic_path(
                        ctx,
                        value,
                        tool_paths=tool_paths,
                        bindings=bindings,
                        extra_paths={
                            self.plan_engine.exclude_file: "${EXCLUDE_FILE}",
                        },
                    )
                    for value in symbolic["argv"]
                ]
            if symbolic.get("cwd") is not None:
                symbolic["cwd"] = _symbolic_path(
                    ctx,
                    symbolic["cwd"],
                    tool_paths=tool_paths,
                    bindings=bindings,
                    extra_paths={
                        self.plan_engine.exclude_file: "${EXCLUDE_FILE}",
                    },
                )
            symbolic_commands.append(symbolic)
        mutation_authority = (
            {
                "authority": "chroot-transaction-journal",
                "mutation_id": "target-filesystem-package-lifecycle",
                "path": "${FILESYSTEM_ROOT}",
                "postcondition": "transaction-cleanup-recorded",
                "precondition": "legacy-extracted-profile-accepted",
                "rollback": "package-state-is-not-fully-rollbackable",
            },
            {
                "authority": "exact-symbolic-paths",
                "mutation_id": "legacy-media-metadata",
                "paths": [
                    "${ISO_ROOT}/.disk",
                    "${ISO_ROOT}/casper",
                    "${ISO_ROOT}/README.diskdefines",
                    "${ISO_ROOT}/md5sum.txt",
                ],
                "postcondition": "final-image-input-tree-complete",
                "precondition": "legacy-media-profile-accepted",
                "rollback": "regenerable-workspace-artifacts",
            },
            {
                "authority": "mount-session-journal",
                "mutation_id": "final-publication-pair",
                "paths": [
                    "${WORK_DIR}/<distribution>-<architecture>-<release>.iso",
                    "${WORK_DIR}/<distribution>-<architecture>-<release>.sha256",
                ],
                "postcondition": "sealed-hash-validated-pair",
                "precondition": "candidate-and-prior-pair-custody-accepted",
                "rollback": "crash-durable-prior-pair-preservation",
            },
        )
        reasons = tuple(sorted(set(reasons)))
        projection = {
            "b2a_evidence_digest": b2a_plan.evidence_digest,
            "b2a_plan_digest": b2a_plan.plan_digest,
            "commands": symbolic_commands,
            "kernel": kernel,
            "metadata": metadata,
            "mutation_authority": mutation_authority,
            "operation": OPERATION_COMPLETE_REBUILD,
            "publication_nonce": publication_nonce,
            "reasons": list(reasons),
            "schema_version": SCHEMA_VERSION,
            "session_token": session_token,
            "support_tools": support_tools,
        }
        lifecycle_digest = _sha256_json(projection)
        decision = (
            DECISION_GRANTED
            if not reasons and b2a_plan.factory_authority_granted
            else DECISION_REFUSED
        )
        grant_id = (
            hashlib.sha256(
                (
                    "liveusb-complete-rebuild-grant-v1\x00"
                    + lifecycle_digest
                ).encode("ascii")
            ).hexdigest()
            if decision == DECISION_GRANTED
            else None
        )
        receipt_payload = {
            "b2a_plan_digest": b2a_plan.plan_digest,
            "commands": symbolic_commands,
            "commands_executed": 0,
            "decision": decision,
            "factory_authority_granted": decision == DECISION_GRANTED,
            "grant_id": grant_id,
            "lifecycle_digest": lifecycle_digest,
            "operation": OPERATION_COMPLETE_REBUILD,
            "phase": "1E-B2B",
            "privileged_operations_executed": 0,
            "reasons": list(reasons),
            "schema_version": SCHEMA_VERSION,
            "single_use": True,
            "state_change_revokes_authority": True,
            "mutation_authority": list(mutation_authority),
        }
        return CompleteRebuildAuthorization(
            decision=decision,
            reasons=reasons,
            session_token=session_token,
            publication_nonce=publication_nonce,
            bindings=bindings,
            b2a_plan=b2a_plan,
            tool_paths=tool_paths,
            support_tools=support_tools,
            metadata=metadata,
            kernel=kernel,
            mutation_authority=mutation_authority,
            exact_commands=tuple(exact_commands),
            symbolic_commands=tuple(symbolic_commands),
            lifecycle_digest=lifecycle_digest,
            grant_id=grant_id,
            receipt=factory_plan.FactoryReceipt(receipt_payload),
        )


class RebuildCommandExecutor:
    """Execute only commands authorized by one fresh rebuild grant."""

    def __init__(
        self,
        ctx,
        authorization,
        runner=None,
        mountinfo_reader=None,
        lease_descriptor=None,
    ):
        if not isinstance(authorization, CompleteRebuildAuthorization):
            raise TypeError("Complete rebuild authorization type is invalid")
        if not authorization.factory_authority_granted:
            raise FactoryExecutionError("Complete rebuild authority is absent")
        self.ctx = ctx
        self.authorization = authorization
        self.runner = backend_run if runner is None else runner
        self.mountinfo_reader = mountinfo_reader
        self.lease_descriptor = lease_descriptor
        if lease_descriptor is not None:
            lease_state = os.fstat(lease_descriptor)
            if not stat.S_ISREG(lease_state.st_mode):
                raise FactoryExecutionError(
                    "Factory process lease descriptor is invalid"
                )
        self.publication_nonce = authorization.publication_nonce
        self._records = []
        self._planned = {
            command.stage: command
            for command in authorization.b2a_plan.commands
        }
        self._planned_used = set()
        self._architecture_used = False
        self._mount_commands = [
            tuple(item["argv"])
            for item in authorization.exact_commands
            if str(item.get("stage", "")).startswith("system-mount-")
        ]
        self._mount_index = 0
        self._kernel_targets = [
            tuple(item["argv"])
            for item in authorization.exact_commands
            if str(item.get("stage", "")).startswith("kernel-target-")
        ]
        self._kernel_index = 0
        self._active_chroot = None
        self._private_paths = {}
        squash_command = self._planned.get("squashfs-build")
        if squash_command is not None and "-ef" in squash_command.argv:
            index = squash_command.argv.index("-ef")
            if index + 1 < len(squash_command.argv):
                self._private_paths[squash_command.argv[index + 1]] = (
                    "${EXCLUDE_FILE}"
                )

    @property
    def records(self):
        return tuple(copy.deepcopy(self._records))

    def tool_path(self, tool):
        try:
            return self.authorization.tool_paths[tool]
        except KeyError as error:
            raise FactoryExecutionError(
                "Authorized tool path is absent: " + tool
            ) from error

    def _record(self, stage, command, result=None, error=None, authority="exact"):
        returncode = None
        if result is not None:
            returncode = getattr(result, "returncode", None)
        record = {
            "argv": [
                _symbolic_path(
                    self.ctx,
                    value,
                    tool_paths=self.authorization.tool_paths,
                    bindings=self.authorization.bindings,
                    extra_paths=self._private_paths,
                )
                for value in command
            ],
            "authority": authority,
            "error_type": None if error is None else type(error).__name__,
            "returncode": returncode,
            "stage": stage,
        }
        self._records.append(record)

    def _invoke(self, stage, command, *, authority="exact", **options):
        selected_options = dict(options)
        if self.lease_descriptor is not None:
            pass_fds = tuple(selected_options.get("pass_fds", ()))
            if self.lease_descriptor not in pass_fds:
                pass_fds = pass_fds + (self.lease_descriptor,)
            selected_options["pass_fds"] = pass_fds
            selected_options["close_fds"] = True
        try:
            result = self.runner(list(command), **selected_options)
        except BaseException as error:
            self._record(
                stage,
                command,
                error=error,
                authority=authority,
            )
            raise
        self._record(
            stage,
            command,
            result=result,
            authority=authority,
        )
        return result

    def execute_exact(self, stage, command, **options):
        command = tuple(command)
        if stage != "target-architecture-observation":
            raise FactoryExecutionError("Exact execution stage is invalid")
        expected = tuple(
            item["argv"]
            for item in self.authorization.exact_commands
            if item.get("stage") == stage
        )
        if self._architecture_used or expected != (command,):
            raise FactoryExecutionError(
                "Target architecture command differs from authority"
            )
        self._architecture_used = True
        return self._invoke(stage, command, **options)

    def execute_planned(self, stage, **options):
        command = self._planned.get(stage)
        if command is None or stage in self._planned_used:
            raise FactoryExecutionError(
                "Planned command stage is absent or already consumed: "
                + stage
            )
        self._planned_used.add(stage)
        return self._invoke(
            stage,
            command.argv,
            **options,
        )

    def assert_publication_candidate(self, path):
        if path != self.authorization.bindings.publication_candidate:
            raise FactoryExecutionError(
                "Publication candidate differs from the grant"
            )

    def validate_architecture(self, architecture):
        if architecture != self.authorization.metadata["architecture"]:
            raise FactoryExecutionError(
                "Observed target architecture differs from the grant"
            )

    def validate_distribution(self, distribution_id, release):
        if (
            distribution_id != self.authorization.metadata["distribution_id"]
            or release != self.authorization.metadata["release"]
        ):
            raise FactoryExecutionError(
                "Observed distribution metadata differs from the grant"
            )

    def build_squashfs(self, output_path):
        expected_output = os.path.join(
            os.path.abspath(self.ctx.iso_dir),
            "casper",
            "filesystem.squashfs",
        )
        if output_path != expected_output:
            raise FactoryExecutionError(
                "SquashFS output differs from the grant"
            )
        probe_source = self.authorization.bindings.probe_source
        probe_output = self.authorization.bindings.probe_output
        probe_root = os.path.dirname(probe_source)
        if os.path.lexists(probe_root):
            raise FactoryExecutionError(
                "Compression probe root already exists"
            )
        os.mkdir(probe_root, 0o700)
        try:
            os.mkdir(probe_source, 0o700)
            probe_result = self.execute_planned(
                "squashfs-capability-probe",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            supported = (
                getattr(probe_result, "returncode", 1) == 0
                and os.path.isfile(probe_output)
                and not os.path.islink(probe_output)
            )
            if supported != self.authorization.bindings.compression_supported:
                raise FactoryExecutionError(
                    "Compression capability changed after authorization"
                )
        finally:
            if os.path.isfile(probe_output) and not os.path.islink(probe_output):
                os.remove(probe_output)
            if os.path.isdir(probe_source) and not os.path.islink(probe_source):
                os.rmdir(probe_source)
            if os.path.isdir(probe_root) and not os.path.islink(probe_root):
                os.rmdir(probe_root)
        result = self.execute_planned("squashfs-build")
        if getattr(result, "returncode", 1) != 0:
            raise FactoryExecutionError("SquashFS build command failed")
        return result

    def _mount_runner(self, command):
        command = tuple(command)
        if (
            self._mount_index >= len(self._mount_commands)
            or command != self._mount_commands[self._mount_index]
        ):
            raise FactoryExecutionError(
                "Mount command differs from the lifecycle plan"
            )
        self._mount_index += 1
        return self._invoke(
            "system-mount-{}".format(self._mount_index),
            command,
        )

    def _unmount_runner(self, command):
        command = tuple(command)
        if (
            len(command) != 3
            or command[0] != self.tool_path("umount")
            or command[1] not in {"-f", "-fl"}
            or not (
                _path_within(
                    os.path.abspath(self.ctx.fs_dir),
                    command[2],
                    include_root=True,
                )
                or _path_within(
                    os.path.abspath(self.ctx.mount_dir),
                    command[2],
                    include_root=False,
                )
            )
        ):
            raise FactoryExecutionError(
                "Identity-derived unmount command is outside authority"
            )
        return self._invoke(
            "identity-derived-unmount",
            command,
            authority="mount-session-journal",
        )

    def mount_session(self, ctx, recovery=False):
        return mount_session.MountSession(
            ctx,
            mountinfo_reader=self.mountinfo_reader,
            mount_runner=self._mount_runner,
            unmount_runner=self._unmount_runner,
            owner_token=self.authorization.session_token,
            mount_executable=self.tool_path("mount"),
            unmount_executable=self.tool_path("umount"),
        )

    def begin_chroot_transaction(self, target, service_targets):
        if self._active_chroot is not None:
            raise FactoryExecutionError("Nested chroot authority is invalid")
        if self._kernel_index >= len(self._kernel_targets):
            raise FactoryExecutionError("Unexpected kernel target command")
        prefix = FactoryExecutionEngine._chroot_prefix(
            self.ctx,
            self.tool_path("chroot"),
        )
        expected = self._kernel_targets[self._kernel_index]
        if prefix + tuple(target) != expected:
            raise FactoryExecutionError(
                "Kernel target command differs from the grant"
            )
        allowed_stubs = []
        for path in service_targets:
            path = os.path.abspath(path)
            if not _path_within(
                os.path.abspath(self.ctx.fs_dir),
                path,
                include_root=False,
            ):
                raise FactoryExecutionError(
                    "Service block target escapes filesystem custody"
                )
            allowed_stubs.append(
                path[len(os.path.abspath(self.ctx.fs_dir)):]
            )
        self._active_chroot = {
            "allowed_stubs": tuple(allowed_stubs),
            "target": expected,
            "target_used": False,
        }

    def end_chroot_transaction(self):
        if self._active_chroot is None:
            raise FactoryExecutionError("Chroot authority is not active")
        if self._active_chroot["target_used"]:
            self._kernel_index += 1
        self._active_chroot = None

    def run(self, stage, command, **options):
        if self._active_chroot is None:
            raise FactoryExecutionError("Chroot command lacks active authority")
        command = tuple(command)
        prefix = FactoryExecutionEngine._chroot_prefix(
            self.ctx,
            self.tool_path("chroot"),
        )
        if command[:len(prefix)] != prefix:
            raise FactoryExecutionError("Chroot command prefix is invalid")
        tail = command[len(prefix):]
        authority = "chroot-transaction-journal"
        if stage == "chroot-target":
            if (
                self._active_chroot["target_used"]
                or command != self._active_chroot["target"]
            ):
                raise FactoryExecutionError("Chroot target differs from authority")
            self._active_chroot["target_used"] = True
            authority = "exact"
        elif stage == "chroot-locale":
            if tail != ("locale-gen", self.ctx.locales):
                raise FactoryExecutionError("Locale command differs from authority")
        elif stage == "chroot-service-stub":
            if (
                len(tail) != 4
                or tail[:3] != ("ln", "-s", "/bin/true")
                or tail[3] not in self._active_chroot["allowed_stubs"]
            ):
                raise FactoryExecutionError("Service stub differs from authority")
        elif stage == "chroot-apt-helper":
            allowed = {
                ("apt-get", "update", "-qq"),
                ("dpkg", "--configure", "-a"),
                ("apt-get", "install", "-f", "-y", "-q"),
            }
            if tail not in allowed or not self.ctx.apt_helper:
                raise FactoryExecutionError("APT helper differs from authority")
        elif stage == "chroot-cleanup":
            allowed = {
                ("apt-get", "autoremove", "--purge"),
                ("apt-get", "autoclean"),
                ("apt-get", "clean"),
            }
            if tail not in allowed:
                raise FactoryExecutionError("Chroot cleanup differs from authority")
        else:
            raise FactoryExecutionError("Chroot command stage is invalid")
        result = self._invoke(
            stage,
            command,
            authority=authority,
            **options,
        )
        if stage == "chroot-target" and getattr(result, "returncode", 1) != 0:
            raise FactoryExecutionError("Kernel target command failed")
        return result

    def assert_complete(self):
        required_planned = {
            "squashfs-capability-probe",
            "squashfs-build",
            "manifest-query",
            "iso-generation",
            "legacy-isohybrid-mutation",
        }
        if (
            not self._architecture_used
            or self._mount_index != len(self._mount_commands)
            or self._kernel_index != len(self._kernel_targets)
            or self._active_chroot is not None
            or not required_planned.issubset(self._planned_used)
        ):
            raise FactoryExecutionError(
                "Complete rebuild command authority was not fully consumed"
            )


class FactoryRecordStore:
    """Durable one-use grant state under one stable directory lock."""

    def __init__(self, records_dir, expected_owner_uid=None):
        self.records_dir = os.path.abspath(os.fspath(records_dir))
        self.expected_owner_uid = (
            os.geteuid()
            if expected_owner_uid is None
            else int(expected_owner_uid)
        )
        self.lock_path = os.path.join(
            self.records_dir,
            _GLOBAL_LOCK_NAME,
        )
        self._lock_descriptor = None

    @property
    def lease_descriptor(self):
        self._require_lock()
        return self._lock_descriptor

    def __enter__(self):
        state = os.lstat(self.records_dir)
        if (
            os.path.realpath(self.records_dir) != self.records_dir
            or not stat.S_ISDIR(state.st_mode)
            or stat.S_ISLNK(state.st_mode)
            or state.st_uid != self.expected_owner_uid
            or stat.S_IMODE(state.st_mode) & 0o022
        ):
            raise FactoryExecutionError(
                "Factory record directory custody is invalid"
            )
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            lock_state = os.fstat(descriptor)
            if (
                not stat.S_ISREG(lock_state.st_mode)
                or lock_state.st_uid != self.expected_owner_uid
                or stat.S_IMODE(lock_state.st_mode) != 0o600
                or lock_state.st_nlink != 1
            ):
                raise FactoryExecutionError(
                    "Factory record lock custody is invalid"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        self._lock_descriptor = descriptor
        return self

    def __exit__(self, _type, _error, _traceback):
        descriptor = self._lock_descriptor
        self._lock_descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        return False

    def _require_lock(self):
        if self._lock_descriptor is None:
            raise FactoryExecutionError("Factory record lock is not held")

    @staticmethod
    def _write_all(descriptor, payload):
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("Factory record write made no progress")
            offset += written

    def _write_new(self, path, payload, mode=0o600):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, mode)
        try:
            if os.geteuid() == 0 and self.expected_owner_uid != 0:
                os.fchown(descriptor, self.expected_owner_uid, -1)
            self._write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _replace_state(self, bundle, state_value):
        state_path = os.path.join(bundle, "state.json")
        if self._pending_state_paths(bundle):
            raise FactoryExecutionError(
                "Factory state has unresolved pending evidence"
            )
        pending = os.path.join(
            bundle,
            ".state.pending-" + uuid.uuid4().hex,
        )
        payload = (
            _canonical_json(state_value) + "\n"
        ).encode("ascii")
        self._write_new(pending, payload)
        os.replace(pending, state_path)
        self._fsync(bundle)

    @staticmethod
    def _fsync(path):
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def issue(self, authorization):
        self._require_lock()
        if not authorization.factory_authority_granted:
            raise FactoryExecutionError("Refused plan cannot issue a grant")
        bundle = os.path.join(
            self.records_dir,
            _GRANT_PREFIX + authorization.grant_id,
        )
        os.mkdir(bundle, 0o700)
        try:
            grant_payload = authorization.grant_payload()
            self._write_new(
                os.path.join(bundle, "grant.json"),
                (_canonical_json(grant_payload) + "\n").encode("ascii"),
            )
            state_value = {
                "attempt_id": None,
                "grant_id": authorization.grant_id,
                "phase": STATE_ISSUED,
                "previous_sha256": None,
                "schema_version": STATE_SCHEMA_VERSION,
                "sequence": 0,
            }
            self._write_new(
                os.path.join(bundle, "state.json"),
                (_canonical_json(state_value) + "\n").encode("ascii"),
            )
            self._fsync(bundle)
            self._fsync(self.records_dir)
        except BaseException:
            for name in ("state.json", "grant.json"):
                path = os.path.join(bundle, name)
                try:
                    os.unlink(path)
                except OSError:
                    pass
            try:
                os.rmdir(bundle)
            except OSError:
                pass
            raise
        return bundle

    def _read_json(self, path):
        raw, _identity = _read_exact_file(path)
        try:
            value = json.loads(raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FactoryExecutionError("Factory record is corrupt") from error
        if not isinstance(value, dict):
            raise FactoryExecutionError("Factory record is not an object")
        return value

    def read_grant(self, bundle):
        self._require_lock()
        value = self._read_json(os.path.join(bundle, "grant.json"))
        digest = value.pop("document_sha256", None)
        if (
            value.get("schema_version") != GRANT_SCHEMA_VERSION
            or not isinstance(digest, str)
            or _sha256_json(value) != digest
        ):
            raise FactoryExecutionError("Factory grant digest is invalid")
        value["document_sha256"] = digest
        return value

    @staticmethod
    def _pending_state_paths(bundle):
        return tuple(
            sorted(
                glob.glob(os.path.join(bundle, ".state.pending-*"))
            )
        )

    @staticmethod
    def _validate_state_value(value):
        expected_keys = {
            "attempt_id",
            "grant_id",
            "phase",
            "previous_sha256",
            "schema_version",
            "sequence",
        }
        if (
            set(value) != expected_keys
            or value.get("schema_version") != STATE_SCHEMA_VERSION
            or value.get("phase")
            not in (STATE_ISSUED, STATE_CONSUMED) + TERMINAL_STATES
            or _HEX_64.fullmatch(value.get("grant_id", "")) is None
            or type(value.get("sequence")) is not int
            or value["sequence"] < 0
        ):
            raise FactoryExecutionError("Factory state schema is invalid")
        issued = {
            "attempt_id": None,
            "grant_id": value["grant_id"],
            "phase": STATE_ISSUED,
            "previous_sha256": None,
            "schema_version": STATE_SCHEMA_VERSION,
            "sequence": 0,
        }
        issued_digest = _sha256_json(issued)
        phase = value["phase"]
        if phase == STATE_ISSUED:
            valid = value == issued
        elif phase == STATE_REVOKED:
            valid = (
                value["attempt_id"] is None
                and value["previous_sha256"] == issued_digest
                and value["sequence"] == 1
            )
        else:
            attempt_id = value["attempt_id"]
            valid_attempt = (
                isinstance(attempt_id, str)
                and _HEX_32.fullmatch(attempt_id) is not None
            )
            consumed = {
                "attempt_id": attempt_id,
                "grant_id": value["grant_id"],
                "phase": STATE_CONSUMED,
                "previous_sha256": issued_digest,
                "schema_version": STATE_SCHEMA_VERSION,
                "sequence": 1,
            }
            if phase == STATE_CONSUMED:
                valid = valid_attempt and value == consumed
            else:
                valid = (
                    valid_attempt
                    and value["previous_sha256"]
                    == _sha256_json(consumed)
                    and value["sequence"] == 2
                )
        if not valid:
            raise FactoryExecutionError(
                "Factory state transition evidence is invalid"
            )
        return value

    def read_state(self, bundle):
        self._require_lock()
        state_path = os.path.join(bundle, "state.json")
        value = self._validate_state_value(
            self._read_json(state_path)
        )
        pending_paths = self._pending_state_paths(bundle)
        if not pending_paths:
            return value
        if len(pending_paths) != 1:
            raise FactoryExecutionError(
                "Factory state has ambiguous pending evidence"
            )
        pending_path = pending_paths[0]
        pending = self._validate_state_value(
            self._read_json(pending_path)
        )
        transitions = {
            STATE_ISSUED: {STATE_CONSUMED, STATE_REVOKED},
            STATE_CONSUMED: {
                STATE_SUCCEEDED,
                STATE_FAILED,
                STATE_INTERRUPTED,
            },
        }
        if (
            pending["grant_id"] != value["grant_id"]
            or pending["phase"]
            not in transitions.get(value["phase"], set())
            or pending["sequence"] != value["sequence"] + 1
            or pending["previous_sha256"] != _sha256_json(value)
        ):
            raise FactoryExecutionError(
                "Pending factory state transition is invalid"
            )
        os.replace(pending_path, state_path)
        self._fsync(bundle)
        return pending

    def consume(self, bundle, grant_id):
        self._require_lock()
        state_value = self.read_state(bundle)
        if (
            state_value["phase"] != STATE_ISSUED
            or state_value["grant_id"] != grant_id
        ):
            raise FactoryExecutionError("Factory grant is not consumable")
        previous = _sha256_json(state_value)
        state_value.update(
            {
                "attempt_id": uuid.uuid4().hex,
                "phase": STATE_CONSUMED,
                "previous_sha256": previous,
                "sequence": state_value["sequence"] + 1,
            }
        )
        self._replace_state(bundle, state_value)
        return state_value["attempt_id"]

    def revoke(self, bundle, receipt):
        self._require_lock()
        state_value = self.read_state(bundle)
        if state_value["phase"] != STATE_ISSUED:
            raise FactoryExecutionError("Factory grant is not revocable")
        outcome_path = os.path.join(bundle, "outcome.json")
        factory_plan.write_receipt(
            outcome_path,
            receipt,
            expected_owner_uid=self.expected_owner_uid,
        )
        previous = _sha256_json(state_value)
        state_value.update(
            {
                "phase": STATE_REVOKED,
                "previous_sha256": previous,
                "sequence": state_value["sequence"] + 1,
            }
        )
        self._replace_state(bundle, state_value)
        return outcome_path

    def finalize(self, bundle, phase, receipt):
        self._require_lock()
        if phase not in TERMINAL_STATES:
            raise ValueError("Factory terminal phase is invalid")
        state_value = self.read_state(bundle)
        if state_value["phase"] != STATE_CONSUMED:
            raise FactoryExecutionError("Factory grant is not consumed")
        outcome_path = os.path.join(bundle, "outcome.json")
        factory_plan.write_receipt(
            outcome_path,
            receipt,
            expected_owner_uid=self.expected_owner_uid,
        )
        previous = _sha256_json(state_value)
        state_value.update(
            {
                "phase": phase,
                "previous_sha256": previous,
                "sequence": state_value["sequence"] + 1,
            }
        )
        self._replace_state(bundle, state_value)
        return outcome_path

    def reconcile_terminal_outcome(self, bundle):
        """Finish a terminal state write after a durable outcome publication."""

        self._require_lock()
        state_value = self.read_state(bundle)
        outcome_path = os.path.join(bundle, "outcome.json")
        if not os.path.lexists(outcome_path):
            if state_value["phase"] in TERMINAL_STATES:
                raise FactoryExecutionError(
                    "Terminal factory state lacks its outcome receipt"
                )
            return None
        outcome = self._read_json(outcome_path)
        phase = outcome.get("status")
        if (
            outcome.get("schema_version") != OUTCOME_SCHEMA_VERSION
            or phase not in TERMINAL_STATES
            or outcome.get("grant_id") != state_value["grant_id"]
        ):
            raise FactoryExecutionError(
                "Durable outcome receipt does not match factory state"
            )
        current_phase = state_value["phase"]
        if current_phase in TERMINAL_STATES:
            if phase != current_phase:
                raise FactoryExecutionError(
                    "Terminal outcome phase differs from factory state"
                )
            return factory_plan.FactoryReceipt(outcome)
        if (
            current_phase == STATE_ISSUED
            and phase != STATE_REVOKED
        ) or (
            current_phase == STATE_CONSUMED
            and phase == STATE_REVOKED
        ):
            raise FactoryExecutionError(
                "Durable outcome transition is invalid"
            )
        if current_phase not in {STATE_ISSUED, STATE_CONSUMED}:
            raise FactoryExecutionError(
                "Factory state cannot reconcile an outcome"
            )
        previous = _sha256_json(state_value)
        state_value.update(
            {
                "phase": phase,
                "previous_sha256": previous,
                "sequence": state_value["sequence"] + 1,
            }
        )
        self._replace_state(bundle, state_value)
        return factory_plan.FactoryReceipt(outcome)

    def incomplete_bundles(self):
        self._require_lock()
        bundles = []
        for name in sorted(os.listdir(self.records_dir)):
            if not name.startswith(_GRANT_PREFIX):
                continue
            path = os.path.join(self.records_dir, name)
            state = os.lstat(path)
            if not stat.S_ISDIR(state.st_mode) or stat.S_ISLNK(state.st_mode):
                raise FactoryExecutionError("Factory bundle custody is invalid")
            phase = self.read_state(path)["phase"]
            if phase in {STATE_ISSUED, STATE_CONSUMED}:
                bundles.append((path, phase))
        return tuple(bundles)


def _residual_evidence(ctx):
    blocked = 0
    if os.path.isdir(ctx.fs_dir):
        for _root, _directories, files in os.walk(ctx.fs_dir):
            blocked += sum(name.endswith(".blocked") for name in files)
    return {
        "blocked_files": blocked,
        "chroot_journal_present": os.path.lexists(
            os.path.join(
                os.path.abspath(ctx.work_dir),
                ".liveusb-chroot-transaction.json",
            )
        ),
        "mount_journal_present": os.path.lexists(
            os.path.join(
                os.path.abspath(ctx.runtime_dir),
                "mount-session.json",
            )
        ),
    }


def _outcome_receipt(
    authorization,
    status,
    records,
    residuals,
    error=None,
    recovered=False,
):
    payload = {
        "actual_commands": list(records),
        "commands_executed": len(records),
        "error_type": None if error is None else type(error).__name__,
        "factory_authority_granted": True,
        "grant_id": authorization.grant_id,
        "lifecycle_digest": authorization.lifecycle_digest,
        "operation": OPERATION_COMPLETE_REBUILD,
        "original_command_outcomes_available": True,
        "privileged_operations_executed": len(records),
        "recovered_without_replay": bool(recovered),
        "residuals": residuals,
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "status": status,
    }
    return factory_plan.FactoryReceipt(payload)


def issue_complete_rebuild(
    ctx,
    records_dir,
    *,
    engine=None,
):
    """Create one durable issued grant from fresh root-free evidence."""

    selected_engine = FactoryExecutionEngine() if engine is None else engine
    with FactoryRecordStore(records_dir) as store:
        incomplete = store.incomplete_bundles()
        if incomplete:
            raise FactoryExecutionError(
                "An incomplete factory grant already exists"
            )
        authorization = selected_engine.plan_complete_rebuild(ctx)
        if not authorization.factory_authority_granted:
            return authorization, None, None
        bundle = store.issue(authorization)
        grant = store.read_grant(bundle)
        if grant != authorization.grant_payload():
            raise FactoryExecutionError("Persisted factory grant changed")
        return authorization, bundle, authorization.receipt


def _validate_bundle_path(bundle):
    bundle = os.path.abspath(os.fspath(bundle))
    records_dir = os.path.dirname(bundle)
    try:
        records_state = os.lstat(records_dir)
        bundle_state = os.lstat(bundle)
    except OSError as error:
        raise FactoryExecutionError("Factory bundle is unavailable") from error
    if (
        not os.path.basename(bundle).startswith(_GRANT_PREFIX)
        or not _path_within(records_dir, bundle)
        or os.path.realpath(records_dir) != records_dir
        or os.path.realpath(bundle) != bundle
        or not stat.S_ISDIR(records_state.st_mode)
        or not stat.S_ISDIR(bundle_state.st_mode)
        or stat.S_ISLNK(records_state.st_mode)
        or stat.S_ISLNK(bundle_state.st_mode)
        or bundle_state.st_uid != records_state.st_uid
        or stat.S_IMODE(bundle_state.st_mode) != 0o700
    ):
        raise FactoryExecutionError("Factory bundle path is invalid")
    return records_dir, bundle, records_state.st_uid


def _revocation_receipt(grant, fresh_authorization):
    return factory_plan.FactoryReceipt(
        {
            "actual_commands": [],
            "commands_executed": 0,
            "factory_authority_granted": False,
            "grant_id": grant.get("grant_id"),
            "operation": OPERATION_COMPLETE_REBUILD,
            "privileged_operations_executed": 0,
            "reasons": ["fresh-evidence-no-longer-matches-issued-grant"],
            "schema_version": OUTCOME_SCHEMA_VERSION,
            "status": STATE_REVOKED,
            "superseding_decision": fresh_authorization.decision,
            "superseding_lifecycle_digest": (
                fresh_authorization.lifecycle_digest
            ),
        }
    )


def execute_issued_rebuild(
    ctx,
    bundle,
    *,
    engine=None,
    runner=None,
    mountinfo_reader=None,
    rebuild_runner=None,
):
    """Consume and execute one issued grant after full fresh recollection."""

    records_dir, bundle, owner_uid = _validate_bundle_path(bundle)
    selected_engine = FactoryExecutionEngine() if engine is None else engine
    selected_rebuild = rebuild.run_rebuild if rebuild_runner is None else rebuild_runner
    with FactoryRecordStore(
        records_dir,
        expected_owner_uid=owner_uid,
    ) as store:
        state_value = store.read_state(bundle)
        reconciled = store.reconcile_terminal_outcome(bundle)
        if reconciled is not None:
            raise FactoryExecutionError(
                "Factory grant already has a terminal outcome"
            )
        if state_value["phase"] != STATE_ISSUED:
            raise FactoryExecutionError("Factory grant has already been used")
        grant = store.read_grant(bundle)
        fresh = selected_engine.plan_complete_rebuild(
            ctx,
            session_token=grant.get("session_token"),
            publication_nonce=grant.get("publication_nonce"),
        )
        if grant != fresh.grant_payload():
            receipt = _revocation_receipt(grant, fresh)
            store.revoke(bundle, receipt)
            return fresh, bundle, receipt
        authorization = fresh
        store.consume(bundle, authorization.grant_id)
        executor = RebuildCommandExecutor(
            ctx,
            authorization,
            runner=runner,
            mountinfo_reader=mountinfo_reader,
            lease_descriptor=store.lease_descriptor,
        )
        try:
            selected_rebuild(ctx, executor=executor)
            executor.assert_complete()
        except BaseException as error:
            receipt = _outcome_receipt(
                authorization,
                STATE_FAILED,
                executor.records,
                _residual_evidence(ctx),
                error=error,
            )
            try:
                store.finalize(bundle, STATE_FAILED, receipt)
            except BaseException as finalization_error:
                raise finalization_error from error
            raise
        receipt = _outcome_receipt(
            authorization,
            STATE_SUCCEEDED,
            executor.records,
            _residual_evidence(ctx),
        )
        store.finalize(bundle, STATE_SUCCEEDED, receipt)
        return authorization, bundle, receipt


class _RecoveryExecutor:
    """Permit only journal-derived cleanup for one consumed grant."""

    def __init__(
        self,
        ctx,
        grant,
        runner=None,
        mountinfo_reader=None,
        lease_descriptor=None,
    ):
        self.ctx = ctx
        self.grant = grant
        self.runner = backend_run if runner is None else runner
        self.mountinfo_reader = mountinfo_reader
        self.lease_descriptor = lease_descriptor
        if lease_descriptor is not None:
            lease_state = os.fstat(lease_descriptor)
            if not stat.S_ISREG(lease_state.st_mode):
                raise FactoryExecutionError(
                    "Factory recovery lease descriptor is invalid"
                )
        self.records = []
        self.publication_nonce = grant["publication_nonce"]
        self._mount_path = preflight_runtime._default_resolver("mount")
        self._unmount_path = preflight_runtime._default_resolver("umount")
        if self._mount_path is None or self._unmount_path is None:
            raise FactoryExecutionError(
                "Recovery mount tooling is unavailable"
            )
        self._tool_paths = {
            "mount": self._mount_path,
            "umount": self._unmount_path,
        }

    def _run(self, command):
        command = tuple(command)
        options = {}
        if self.lease_descriptor is not None:
            options.update(
                {
                    "close_fds": True,
                    "pass_fds": (self.lease_descriptor,),
                }
            )
        result = self.runner(list(command), **options)
        self.records.append(
            {
                "argv": [
                    _symbolic_path(
                        self.ctx,
                        value,
                        tool_paths=self._tool_paths,
                    )
                    for value in command
                ],
                "authority": "mount-session-journal",
                "error_type": None,
                "returncode": getattr(result, "returncode", None),
                "stage": "recovery-cleanup",
            }
        )
        return result

    def mount_session(self, ctx, recovery=False):
        return mount_session.MountSession(
            ctx,
            mountinfo_reader=self.mountinfo_reader,
            mount_runner=self._run,
            unmount_runner=self._run,
            owner_token=self.grant["session_token"],
            mount_executable=self._mount_path,
            unmount_executable=self._unmount_path,
        )


def recover_consumed_rebuild(
    ctx,
    bundle,
    *,
    runner=None,
    mountinfo_reader=None,
    recovery_runner=None,
):
    """Recover one consumed attempt without executing new factory work."""

    records_dir, bundle, owner_uid = _validate_bundle_path(bundle)
    selected_recovery = (
        rebuild.recover_rebuild
        if recovery_runner is None
        else recovery_runner
    )
    with FactoryRecordStore(
        records_dir,
        expected_owner_uid=owner_uid,
    ) as store:
        state_value = store.read_state(bundle)
        reconciled = store.reconcile_terminal_outcome(bundle)
        if reconciled is not None:
            return bundle, reconciled
        if state_value["phase"] != STATE_CONSUMED:
            raise FactoryExecutionError(
                "Only a consumed factory grant can enter recovery"
            )
        grant = store.read_grant(bundle)
        executor = _RecoveryExecutor(
            ctx,
            grant,
            runner=runner,
            mountinfo_reader=mountinfo_reader,
            lease_descriptor=store.lease_descriptor,
        )
        recovered = selected_recovery(ctx, executor=executor)
        if isinstance(recovered, Mapping):
            recovered_chroot = bool(
                recovered.get("chroot_transaction")
            )
            recovered_publication = bool(recovered.get("publication"))
        else:
            recovered_chroot = False
            recovered_publication = bool(recovered)
        receipt = factory_plan.FactoryReceipt(
            {
                "actual_commands": list(executor.records),
                "commands_executed": len(executor.records),
                "factory_authority_granted": False,
                "grant_id": grant["grant_id"],
                "operation": OPERATION_COMPLETE_REBUILD,
                "original_command_outcomes_available": False,
                "privileged_operations_executed": len(executor.records),
                "recovered_chroot_transaction": recovered_chroot,
                "recovered_publication": recovered_publication,
                "replayed_factory_commands": 0,
                "residuals": _residual_evidence(ctx),
                "schema_version": OUTCOME_SCHEMA_VERSION,
                "status": STATE_INTERRUPTED,
            }
        )
        store.finalize(bundle, STATE_INTERRUPTED, receipt)
        return bundle, receipt
