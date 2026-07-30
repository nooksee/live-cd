"""Observation-only readiness reporting for the legacy LiveUSB factory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import stat
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

from . import mount_session
from . import mounts
from . import rebuild
from . import transaction


SCHEMA_VERSION = "liveusb.preflight.v1"
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARNING = "warning"
STATUS_UNKNOWN = "unknown"
STATUS_SKIPPED = "skipped"
STATUS_ORDER = (
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_WARNING,
    STATUS_UNKNOWN,
    STATUS_SKIPPED,
)

_MAX_JOURNAL_BYTES = 4 * 1024 * 1024
_MAX_CHROOT_LOCK_BYTES = 1024 * 1024
_MAX_MEMINFO_BYTES = 1024 * 1024
_MAX_SIDECAR_BYTES = 4096
_MOUNT_JOURNAL_VERSION = mount_session._JOURNAL_VERSION
_CHROOT_JOURNAL_VERSION = transaction._JOURNAL_VERSION
_CHROOT_LOCK_VERSION = transaction._LOCK_VERSION
_FINDING_ID = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_LOCK_LINE = re.compile(
    r"^\d+:\s+(?:->\s+)?(?P<kind>\S+)\s+\S+\s+"
    r"(?P<mode>\S+)\s+(?P<pid>-?\d+)\s+"
    r"(?P<major>[0-9a-fA-F]+):(?P<minor>[0-9a-fA-F]+):"
    r"(?P<inode>\d+)\s+"
)
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|credential|authorization|"
    r"cookie|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|credential|authorization|"
    r"cookie|api[_-]?key|private[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_URI_USERINFO = re.compile(r"(://)[^/@\s]+@")


@dataclass(frozen=True)
class DependencySpec:
    """One executable required by a bounded factory stage."""

    command: str
    purpose: str
    required: bool = True


DEFAULT_DEPENDENCIES = (
    DependencySpec("unsquashfs", "legacy extraction"),
    DependencySpec("mksquashfs", "legacy rebuild"),
    DependencySpec("rsync", "extracted media copy"),
    DependencySpec("genisoimage", "legacy ISO generation"),
    DependencySpec("isohybrid", "legacy hybrid finalization"),
    DependencySpec("chroot", "target command execution"),
    DependencySpec("mount", "filesystem attachment"),
    DependencySpec("umount", "filesystem cleanup"),
)

_FACTORY_STAGES = (
    "operation-custody-acquisition",
    "squashfs-capability-probe",
    "squashfs-build",
    "iso-generation",
    "legacy-isohybrid-mutation",
    "read-only-seal",
    "final-byte-sha256",
    "sidecar-preparation",
    "crash-durable-pair-publication",
    "publication-validation-and-acknowledgement",
)


def _sanitize_string(value):
    value = _URI_USERINFO.sub(r"\1<redacted>@", value)
    value = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: match.group(1) + "=<redacted>",
        value,
    )
    return _BEARER_VALUE.sub("Bearer <redacted>", value)


def _sanitize(value, key=None):
    if key is not None and _SENSITIVE_KEY.search(str(key)):
        return "<redacted>"
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, os.PathLike):
        return _sanitize_string(os.fspath(value))
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(item_value, key=item_key)
            for item_key, item_value in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    return "<{}>".format(type(value).__name__)


@dataclass(frozen=True)
class Finding:
    """One independent preflight observation."""

    check_id: str
    subsystem: str
    status: str
    summary: str
    evidence: Mapping[str, Any]
    remediation: str

    def __post_init__(self):
        if not _FINDING_ID.fullmatch(self.check_id):
            raise ValueError("Preflight finding identifier is invalid")
        if self.status not in STATUS_ORDER:
            raise ValueError("Preflight finding status is invalid")
        if not isinstance(self.subsystem, str) or not self.subsystem:
            raise ValueError("Preflight finding subsystem is required")
        if not isinstance(self.summary, str) or not self.summary:
            raise ValueError("Preflight finding summary is required")
        if not isinstance(self.evidence, Mapping) or not self.evidence:
            raise ValueError("Preflight finding evidence is required")
        if not isinstance(self.remediation, str) or not self.remediation:
            raise ValueError("Preflight finding remediation is required")

    def to_dict(self):
        return {
            "check_id": self.check_id,
            "evidence": _sanitize(self.evidence),
            "remediation": _sanitize_string(self.remediation),
            "status": self.status,
            "subsystem": self.subsystem,
            "summary": _sanitize_string(self.summary),
        }


@dataclass(frozen=True)
class PreflightReport:
    """Stable collection of findings without a collapsed verdict."""

    findings: Tuple[Finding, ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        findings = tuple(self.findings)
        if any(not isinstance(finding, Finding) for finding in findings):
            raise TypeError("Preflight report contains an invalid finding")
        object.__setattr__(self, "findings", findings)
        identifiers = [finding.check_id for finding in findings]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Preflight finding identifiers are duplicated")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Preflight report schema version is invalid")

    @property
    def counts(self):
        return {
            status: sum(
                finding.status == status
                for finding in self.findings
            )
            for status in STATUS_ORDER
        }

    def to_dict(self):
        return {
            "counts": self.counts,
            "findings": [
                finding.to_dict()
                for finding in self.findings
            ],
            "schema_version": self.schema_version,
        }

    def to_json(self, indent=None):
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
        )

    def render_text(self):
        counts = self.counts
        lines = [
            "LiveUSB preflight findings; no aggregate verdict: "
            + ", ".join(
                "{}={}".format(status, counts[status])
                for status in STATUS_ORDER
            )
        ]
        for finding in self.findings:
            finding_value = finding.to_dict()
            evidence = json.dumps(
                finding_value["evidence"],
                sort_keys=True,
                separators=(",", ":"),
            )
            lines.append(
                "[{}] {}: {} | evidence={} | remediation={}".format(
                    finding.status.upper(),
                    finding.check_id,
                    finding_value["summary"],
                    evidence,
                    finding_value["remediation"],
                )
            )
        return "\n".join(lines)


def _error_evidence(error):
    return {"error_type": type(error).__name__}


def _node_kind(mode):
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISBLK(mode):
        return "block-device"
    if stat.S_ISCHR(mode):
        return "character-device"
    return "other"


def _state_record(state):
    return {
        "device": state.st_dev,
        "group_gid": state.st_gid,
        "inode": state.st_ino,
        "kind": _node_kind(state.st_mode),
        "link_count": state.st_nlink,
        "mode": "{:04o}".format(stat.S_IMODE(state.st_mode)),
        "owner_uid": state.st_uid,
        "size_bytes": state.st_size,
    }


def _file_identity(state):
    return (
        state.st_dev,
        state.st_ino,
        state.st_mode,
        state.st_nlink,
        state.st_uid,
        state.st_gid,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def _file_identity_record(state):
    return {
        "device": state.st_dev,
        "group_gid": state.st_gid,
        "inode": state.st_ino,
        "link_count": state.st_nlink,
        "mode": state.st_mode,
        "mtime_ns": state.st_mtime_ns,
        "owner_uid": state.st_uid,
        "size_bytes": state.st_size,
        "ctime_ns": state.st_ctime_ns,
    }


def _file_identity_from_record(record):
    return (
        record["device"],
        record["inode"],
        record["mode"],
        record["link_count"],
        record["owner_uid"],
        record["group_gid"],
        record["size_bytes"],
        record["mtime_ns"],
        record["ctime_ns"],
    )


def _path_text(value):
    try:
        return os.fspath(value)
    except TypeError:
        return "<invalid-path-type>"


def _path_syntax_issues(path):
    if not isinstance(path, str):
        return ["path-is-not-text"]
    if not os.path.isabs(path):
        return ["path-is-not-absolute"]
    if os.path.normpath(path) != path:
        return ["path-is-not-normalized"]
    return []


def _literal_chain_issues(path, include_missing_leaf=False):
    issues = []
    if _path_syntax_issues(path):
        return issues
    components = path.split(os.sep)
    cursor = os.sep
    for index, component in enumerate(components):
        if not component:
            continue
        cursor = os.path.join(cursor, component)
        try:
            state = os.lstat(cursor)
        except FileNotFoundError:
            if include_missing_leaf or index < len(components) - 1:
                issues.append("missing-ancestor:{}".format(cursor))
            break
        except OSError:
            issues.append("unreadable-ancestor:{}".format(cursor))
            break
        if stat.S_ISLNK(state.st_mode):
            issues.append("symlink-ancestor:{}".format(cursor))
            break
        if not stat.S_ISDIR(state.st_mode) and cursor != path:
            issues.append("nondirectory-ancestor:{}".format(cursor))
            break
    return issues


def _accepted_literal_chain(path):
    try:
        records = mounts.literal_directory_chain(path)
    except mounts.MountEvidenceError as error:
        evidence = _error_evidence(error)
        evidence["accepted"] = False
        return evidence, ["accepted-literal-chain-rejected"]
    terminal = records[-1]
    return {
        "accepted": True,
        "entry_count": len(records),
        "terminal": {
            "device": terminal["dev"],
            "inode": terminal["ino"],
            "mode": "{:04o}".format(terminal["mode"]),
            "path": terminal["path"],
        },
    }, []


def _runtime_parent_issues(parent):
    issues = []
    if _path_syntax_issues(parent):
        return ["runtime-parent-path-is-invalid"]
    cursor = os.sep
    for component in parent.split(os.sep):
        if not component:
            continue
        cursor = os.path.join(cursor, component)
        try:
            state = os.lstat(cursor)
        except FileNotFoundError:
            issues.append("missing-ancestor:{}".format(cursor))
            break
        except OSError:
            issues.append("unreadable-ancestor:{}".format(cursor))
            break
        if stat.S_ISLNK(state.st_mode):
            issues.append("symlink-ancestor:{}".format(cursor))
            break
        if not stat.S_ISDIR(state.st_mode):
            issues.append("nondirectory-ancestor:{}".format(cursor))
            break
        mode = stat.S_IMODE(state.st_mode)
        if mode & 0o022 and not (state.st_mode & stat.S_ISVTX):
            issues.append("unsafe-writable-ancestor:{}".format(cursor))
            break
    return issues


def _path_within(root, path, include_root=True):
    try:
        common = os.path.commonpath((root, path))
    except (TypeError, ValueError):
        return False
    return common == root and (include_root or path != root)


def _pending_journal_paths(path, marker):
    parent = os.path.dirname(path)
    prefix = os.path.basename(path) + marker
    try:
        with os.scandir(parent) as entries:
            return tuple(
                sorted(
                    os.path.join(parent, entry.name)
                    for entry in entries
                    if entry.name.startswith(prefix)
                )
            )
    except FileNotFoundError:
        return tuple()


def _is_hex(value, length):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _journal_summary(check_id, value, expected_roots):
    issues = []
    metadata = {}
    if check_id == "journal.mount-session":
        expected_keys = {
            "artifacts",
            "directories",
            "external",
            "mounts",
            "owner",
            "phase",
            "previous_sha256",
            "roots",
            "sequence",
            "version",
            "x",
        }
        if not isinstance(value, dict) or set(value) != expected_keys:
            return {}, ["mount-journal-top-level-schema-is-invalid"]
        owner = value["owner"]
        if (
            not isinstance(owner, dict)
            or set(owner) != {"pid", "token"}
            or type(owner["pid"]) is not int
            or owner["pid"] < 1
            or not _is_hex(owner["token"], 32)
        ):
            issues.append("mount-journal-owner-is-invalid")
        if value["version"] != _MOUNT_JOURNAL_VERSION:
            issues.append("mount-journal-version-is-invalid")
        if type(value["sequence"]) is not int or value["sequence"] < 1:
            issues.append("mount-journal-sequence-is-invalid")
        if value["phase"] not in {"active", "cleaning", "complete"}:
            issues.append("mount-journal-phase-is-invalid")
        if value["roots"] != expected_roots:
            issues.append("mount-journal-roots-do-not-match-context")
        if not isinstance(value["mounts"], list):
            issues.append("mount-journal-mounts-are-invalid")
        if not isinstance(value["directories"], list):
            issues.append("mount-journal-directories-are-invalid")
        if not isinstance(value["artifacts"], list):
            issues.append("mount-journal-artifacts-are-invalid")
        if not isinstance(value["x"], dict):
            issues.append("mount-journal-x-state-is-invalid")
        if value["external"] is not None and not isinstance(
            value["external"],
            dict,
        ):
            issues.append("mount-journal-external-state-is-invalid")
        metadata = {
            "artifact_count": len(value["artifacts"])
            if isinstance(value["artifacts"], list)
            else None,
            "directory_count": len(value["directories"])
            if isinstance(value["directories"], list)
            else None,
            "external_present": value["external"] is not None,
            "mount_count": len(value["mounts"])
            if isinstance(value["mounts"], list)
            else None,
            "owner_pid": owner.get("pid")
            if isinstance(owner, dict)
            else None,
            "phase": value["phase"],
            "sequence": value["sequence"],
            "version": value["version"],
        }
        return metadata, issues

    expected_keys = {
        "managed",
        "owner",
        "sequence",
        "services",
        "version",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        return {}, ["chroot-journal-top-level-schema-is-invalid"]
    owner = value["owner"]
    if (
        not isinstance(owner, dict)
        or set(owner) != {"pid", "token"}
        or type(owner["pid"]) is not int
        or owner["pid"] < 1
        or not _is_hex(owner["token"], 32)
    ):
        issues.append("chroot-journal-owner-is-invalid")
    if value["version"] != _CHROOT_JOURNAL_VERSION:
        issues.append("chroot-journal-version-is-invalid")
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        issues.append("chroot-journal-sequence-is-invalid")
    if not isinstance(value["managed"], list):
        issues.append("chroot-journal-managed-records-are-invalid")
    if not isinstance(value["services"], list):
        issues.append("chroot-journal-service-records-are-invalid")
    metadata = {
        "managed_count": len(value["managed"])
        if isinstance(value["managed"], list)
        else None,
        "owner_pid": owner.get("pid")
        if isinstance(owner, dict)
        else None,
        "sequence": value["sequence"],
        "service_count": len(value["services"])
        if isinstance(value["services"], list)
        else None,
        "version": value["version"],
    }
    return metadata, issues


def _read_chroot_lock_metadata(path, observed_state):
    if observed_state.st_size > _MAX_CHROOT_LOCK_BYTES:
        return {}, ["chroot-lock-metadata-exceeds-observation-limit"]
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != observed_state.st_dev
                or opened.st_ino != observed_state.st_ino
                or opened.st_size != observed_state.st_size
            ):
                raise ValueError("Chroot lock identity changed")
            raw = os.read(descriptor, _MAX_CHROOT_LOCK_BYTES + 1)
            final = os.fstat(descriptor)
            if (
                final.st_dev != opened.st_dev
                or final.st_ino != opened.st_ino
                or final.st_size != opened.st_size
            ):
                raise ValueError("Chroot lock changed during observation")
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_CHROOT_LOCK_BYTES:
            raise ValueError("Chroot lock metadata is too large")
        value = json.loads(raw.decode("utf-8"))
    except Exception as error:
        return _error_evidence(error), ["chroot-lock-metadata-is-unreadable"]
    if (
        not isinstance(value, dict)
        or set(value) != {"pid", "token", "version"}
        or value["version"] != _CHROOT_LOCK_VERSION
        or type(value["pid"]) is not int
        or value["pid"] < 1
        or not _is_hex(value["token"], 32)
    ):
        return {}, ["chroot-lock-metadata-schema-is-invalid"]
    return {
        "owner_pid": value["pid"],
        "version": value["version"],
    }, []


def _default_lock_text_reader():
    with open(
        "/proc/locks",
        "r",
        encoding="utf-8",
        errors="strict",
    ) as handle:
        return handle.read(_MAX_JOURNAL_BYTES + 1)


def _default_meminfo_reader():
    with open(
        "/proc/meminfo",
        "r",
        encoding="ascii",
        errors="strict",
    ) as handle:
        return handle.read(_MAX_MEMINFO_BYTES + 1)


def _parse_meminfo(payload):
    if not isinstance(payload, str):
        raise ValueError("Memory evidence is not text")
    if len(payload.encode("ascii")) > _MAX_MEMINFO_BYTES:
        raise ValueError("Memory evidence exceeds the observation limit")
    values = {}
    for line in payload.splitlines():
        if not line:
            continue
        name, separator, remainder = line.partition(":")
        if not separator:
            raise ValueError("Memory evidence contains an invalid line")
        fields = remainder.split()
        if len(fields) != 2 or fields[1] != "kB":
            continue
        try:
            value = int(fields[0])
        except ValueError as error:
            raise ValueError(
                "Memory evidence contains an invalid value"
            ) from error
        if value < 0:
            raise ValueError("Memory evidence contains a negative value")
        values[name] = value * 1024
    required = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    if not required.issubset(values):
        raise ValueError("Memory evidence omits a required field")
    if values["MemAvailable"] > values["MemTotal"]:
        raise ValueError("Memory availability exceeds total memory")
    if values["SwapFree"] > values["SwapTotal"]:
        raise ValueError("Swap availability exceeds total swap")
    return {
        "memory_available_bytes": values["MemAvailable"],
        "memory_available_ratio": (
            values["MemAvailable"] / values["MemTotal"]
            if values["MemTotal"]
            else None
        ),
        "memory_total_bytes": values["MemTotal"],
        "swap_available_bytes": values["SwapFree"],
        "swap_available_ratio": (
            values["SwapFree"] / values["SwapTotal"]
            if values["SwapTotal"]
            else None
        ),
        "swap_total_bytes": values["SwapTotal"],
    }


def _parse_lock_text(payload):
    if not isinstance(payload, str):
        raise ValueError("Lock evidence is not text")
    if len(payload.encode("utf-8")) > _MAX_JOURNAL_BYTES:
        raise ValueError("Lock evidence exceeds the observation limit")
    records = []
    for line in payload.splitlines():
        if not line:
            continue
        match = _LOCK_LINE.match(line)
        if match is None:
            raise ValueError("Lock evidence contains an invalid line")
        records.append(
            {
                "device_major": int(match.group("major"), 16),
                "device_minor": int(match.group("minor"), 16),
                "inode": int(match.group("inode")),
                "kind": match.group("kind"),
                "mode": match.group("mode"),
                "pid": int(match.group("pid")),
            }
        )
    return tuple(records)


class PreflightEngine:
    """Collect readiness evidence without changing host or project state."""

    def __init__(
        self,
        dependencies=DEFAULT_DEPENDENCIES,
        which=None,
        statvfs=None,
        mountinfo_reader=None,
        lock_text_reader=None,
        effective_uid=None,
        expected_owner_uid=None,
        access=None,
        cpu_count_reader=None,
        loadavg_reader=None,
        meminfo_reader=None,
        machine_reader=None,
        kvm_state_reader=None,
        kvm_path="/dev/kvm",
    ):
        self.dependencies = tuple(dependencies)
        self.which = shutil.which if which is None else which
        self.statvfs = os.statvfs if statvfs is None else statvfs
        self.mountinfo_reader = (
            mounts.read_mountinfo
            if mountinfo_reader is None
            else mountinfo_reader
        )
        self.lock_text_reader = (
            _default_lock_text_reader
            if lock_text_reader is None
            else lock_text_reader
        )
        self.effective_uid = (
            os.geteuid
            if effective_uid is None
            else effective_uid
        )
        self.expected_owner_uid = expected_owner_uid
        self.access = os.access if access is None else access
        self.cpu_count_reader = (
            os.cpu_count
            if cpu_count_reader is None
            else cpu_count_reader
        )
        self.loadavg_reader = (
            os.getloadavg
            if loadavg_reader is None
            else loadavg_reader
        )
        self.meminfo_reader = (
            _default_meminfo_reader
            if meminfo_reader is None
            else meminfo_reader
        )
        self.machine_reader = (
            platform.machine
            if machine_reader is None
            else machine_reader
        )
        self.kvm_state_reader = (
            os.lstat
            if kvm_state_reader is None
            else kvm_state_reader
        )
        self.kvm_path = _path_text(kvm_path)

    def inspect(self, ctx):
        findings = []
        effective_uid = int(self.effective_uid())
        owner_uid = (
            effective_uid
            if self.expected_owner_uid is None
            else int(self.expected_owner_uid)
        )
        paths = {
            "work": _path_text(ctx.work_dir),
            "filesystem": _path_text(ctx.fs_dir),
            "iso_tree": _path_text(ctx.iso_dir),
            "mount": _path_text(ctx.mount_dir),
            "runtime": _path_text(ctx.runtime_dir),
            "source_iso": _path_text(ctx.iso),
        }

        findings.extend(self._inspect_privilege(effective_uid))
        work = self._inspect_path(
            "workspace.work-root",
            "workspace",
            paths["work"],
            expected_kind="directory",
            expected_owner=owner_uid,
            require_owner=True,
            require_writable=True,
            missing_status=STATUS_FAIL,
        )
        findings.append(work)
        filesystem = self._inspect_path(
            "workspace.filesystem-root",
            "workspace",
            paths["filesystem"],
            expected_kind="directory",
            expected_owner=owner_uid,
            require_owner=True,
            require_writable=True,
            missing_status=STATUS_SKIPPED,
        )
        iso_tree = self._inspect_path(
            "workspace.iso-root",
            "workspace",
            paths["iso_tree"],
            expected_kind="directory",
            expected_owner=owner_uid,
            require_owner=True,
            require_writable=True,
            missing_status=STATUS_SKIPPED,
        )
        findings.extend((filesystem, iso_tree))
        findings.append(
            self._inspect_workspace_layout(filesystem, iso_tree)
        )
        findings.append(
            self._inspect_workspace_confinement(ctx, paths)
        )
        findings.append(
            self._inspect_mount_root(paths["mount"])
        )
        runtime = self._inspect_runtime_root(
            paths["runtime"],
            owner_uid,
        )
        findings.append(runtime)
        source_finding = self._inspect_source_iso(paths["source_iso"])
        findings.append(source_finding)
        findings.extend(self._inspect_capacity(paths["work"], work))
        findings.extend(self._inspect_dependencies())
        findings.extend(
            self._inspect_source_media_readiness(source_finding)
        )
        findings.extend(self._inspect_qemu_readiness())
        findings.extend(self._inspect_machine_resources())

        mount_finding = self._inspect_mounts(paths)
        findings.append(mount_finding)

        lock_records, lock_error = self._read_lock_records()
        operation_lock = self._inspect_lock(
            "lock.operation",
            os.path.join(paths["runtime"], "operation.lock"),
            owner_uid,
            lock_records,
            lock_error,
            persistent=True,
        )
        findings.append(operation_lock)
        chroot_lock = self._inspect_lock(
            "lock.chroot",
            os.path.join(
                paths["filesystem"],
                "tmp",
                "lock_chroot",
            ),
            owner_uid,
            lock_records,
            lock_error,
            persistent=False,
        )
        findings.append(chroot_lock)
        findings.append(
            self._inspect_journal(
                "journal.mount-session",
                os.path.join(
                    paths["runtime"],
                    "mount-session.json",
                ),
                ".pending-",
                owner_uid,
                {
                    "filesystem": os.path.realpath(paths["filesystem"]),
                    "work": os.path.realpath(paths["work"]),
                },
                operation_lock,
            )
        )
        findings.append(
            self._inspect_journal(
                "journal.chroot-transaction",
                os.path.join(
                    paths["work"],
                    ".liveusb-chroot-transaction.json",
                ),
                ".pending-",
                owner_uid,
                None,
                chroot_lock,
            )
        )
        findings.append(
            self._inspect_legacy_profile(
                paths["filesystem"],
                paths["iso_tree"],
                filesystem,
                iso_tree,
            )
        )
        findings.append(
            self._inspect_prior_publication(
                paths["work"],
                paths["source_iso"],
                work,
                owner_uid,
            )
        )
        findings.append(self._inspect_operation_plan())
        return PreflightReport(tuple(findings))

    def _observe_executable(self, command):
        evidence = {
            "command": command,
            "discovered": None,
            "path": None,
            "version_query_executed": False,
            "version_status": "unobserved-until-phase-1e-b",
        }
        try:
            resolved = self.which(command)
        except Exception as error:
            evidence.update(_error_evidence(error))
            return STATUS_UNKNOWN, evidence
        if resolved is None:
            evidence["discovered"] = False
            return STATUS_FAIL, evidence
        try:
            resolved = os.path.abspath(os.fspath(resolved))
            canonical = os.path.realpath(resolved)
            state = os.stat(canonical)
        except (OSError, TypeError) as error:
            evidence.update(_error_evidence(error))
            evidence["discovered"] = True
            evidence["path"] = _path_text(resolved)
            evidence["valid_executable"] = False
            return STATUS_FAIL, evidence
        issues = []
        if not stat.S_ISREG(state.st_mode):
            issues.append("target-is-not-regular")
        try:
            executable = bool(self.access(canonical, os.X_OK))
        except Exception as error:
            evidence.update(_error_evidence(error))
            evidence.update(
                {
                    "canonical_path": canonical,
                    "discovered": True,
                    "node": _state_record(state),
                    "path": resolved,
                    "valid_executable": None,
                }
            )
            return STATUS_UNKNOWN, evidence
        if not executable:
            issues.append("target-is-not-executable")
        evidence.update(
            {
                "canonical_path": canonical,
                "discovered": True,
                "issues": issues,
                "node": _state_record(state),
                "path": resolved,
                "valid_executable": not issues,
            }
        )
        return STATUS_FAIL if issues else STATUS_PASS, evidence

    def _inspect_privilege(self, effective_uid):
        current = Finding(
            "privilege.current",
            "privilege",
            STATUS_PASS,
            "Current effective privilege was observed.",
            {
                "effective_uid": effective_uid,
                "is_root": effective_uid == 0,
                "preflight_requires_root": False,
            },
            "No privilege change is required for observation-only Phase 1E-A.",
        )

        sudo_status, sudo_evidence = self._observe_executable("sudo")
        sudo_evidence["escalation_test_executed"] = False
        if sudo_status == STATUS_PASS:
            sudo_finding = Finding(
                "privilege.sudo-path",
                "privilege",
                STATUS_PASS,
                "A sudo executable path was discovered without testing escalation.",
                sudo_evidence,
                "Treat path presence only as inventory; Phase 1E-A does not prove sudo usability.",
            )
        elif sudo_status == STATUS_UNKNOWN:
            sudo_finding = Finding(
                "privilege.sudo-path",
                "privilege",
                STATUS_UNKNOWN,
                "Sudo path discovery could not complete.",
                sudo_evidence,
                "Restore executable-path visibility without testing escalation, then repeat preflight.",
            )
        else:
            sudo_finding = Finding(
                "privilege.sudo-path",
                "privilege",
                STATUS_WARNING,
                "No valid sudo executable path was observed.",
                sudo_evidence,
                "Record the intended privilege boundary separately; do not infer that escalation is impossible.",
            )

        authority = Finding(
            "privilege.factory-authorization",
            "privilege",
            STATUS_UNKNOWN,
            "Factory authorization is explicitly absent from Phase 1E-A.",
            {
                "factory_authority_evaluated": False,
                "factory_authority_granted": False,
                "factory_requires_root": True,
                "phase_1e_a_grants_authority": False,
            },
            "Phase 1E-B must resolve capability evidence before a separate authority grants factory execution.",
        )
        return current, sudo_finding, authority

    def _inspect_path(
        self,
        check_id,
        subsystem,
        path,
        expected_kind,
        expected_owner,
        require_owner,
        require_writable,
        missing_status,
        require_single_link=False,
        include_full_identity=False,
    ):
        syntax_issues = _path_syntax_issues(path)
        evidence = {
            "expected_kind": expected_kind,
            "expected_owner_uid": (
                expected_owner if require_owner else None
            ),
            "path": path,
        }
        if syntax_issues:
            evidence["issues"] = syntax_issues
            return Finding(
                check_id,
                subsystem,
                STATUS_FAIL,
                "Path syntax is outside the accepted literal-path contract.",
                evidence,
                "Use one absolute, normalized path without aliases or traversal components.",
            )
        try:
            state = os.lstat(path)
        except FileNotFoundError:
            evidence.update({"exists": False, "issues": ["missing"]})
            summary = (
                "Required path is absent."
                if missing_status == STATUS_FAIL
                else "Path check is skipped because the node is absent."
            )
            return Finding(
                check_id,
                subsystem,
                missing_status,
                summary,
                evidence,
                "Create or select the path only through the separately authorized workspace operation.",
            )
        except OSError as error:
            evidence.update(_error_evidence(error))
            return Finding(
                check_id,
                subsystem,
                STATUS_UNKNOWN,
                "Path identity could not be observed.",
                evidence,
                "Restore read access to the path metadata and repeat preflight.",
            )

        kind = _node_kind(state.st_mode)
        chain_target = path if kind == "directory" else os.path.dirname(path)
        chain_evidence, chain_issues = _accepted_literal_chain(chain_target)
        issues = _literal_chain_issues(path)
        issues.extend(chain_issues)
        if kind != expected_kind:
            issues.append("unexpected-kind")
        if os.path.realpath(path) != path:
            issues.append("canonical-path-mismatch")
        if require_owner and state.st_uid != expected_owner:
            issues.append("owner-mismatch")
        if require_single_link and state.st_nlink != 1:
            issues.append("link-count-is-not-one")
        if require_writable and not self.access(path, os.W_OK | os.X_OK):
            issues.append("not-writable-and-searchable")
        evidence.update(
            {
                "canonical_path": os.path.realpath(path),
                "exists": True,
                "issues": issues,
                "literal_directory_chain": chain_evidence,
                "node": _state_record(state),
            }
        )
        if include_full_identity:
            evidence["initial_identity"] = _file_identity_record(state)
        if issues:
            return Finding(
                check_id,
                subsystem,
                STATUS_FAIL,
                "Path custody does not satisfy the accepted workspace contract.",
                evidence,
                "Correct the listed path, node-type, ownership, link, or access defect before factory authorization.",
            )
        return Finding(
            check_id,
            subsystem,
            STATUS_PASS,
            "Path custody is observable and valid.",
            evidence,
            "No action is required for this path.",
        )

    @staticmethod
    def _inspect_workspace_layout(filesystem, iso_tree):
        fs_exists = bool(filesystem.evidence.get("exists"))
        iso_exists = bool(iso_tree.evidence.get("exists"))
        evidence = {
            "filesystem_exists": fs_exists,
            "filesystem_status": filesystem.status,
            "iso_tree_exists": iso_exists,
            "iso_tree_status": iso_tree.status,
        }
        if not fs_exists and not iso_exists:
            return Finding(
                "workspace.layout",
                "workspace",
                STATUS_PASS,
                "Workspace is empty and has no partial extracted tree.",
                dict(evidence, state="empty"),
                "No cleanup is required before a separately authorized extraction.",
            )
        if (
            fs_exists
            and iso_exists
            and filesystem.status == STATUS_PASS
            and iso_tree.status == STATUS_PASS
        ):
            return Finding(
                "workspace.layout",
                "workspace",
                STATUS_PASS,
                "Workspace contains both extracted-tree roots.",
                dict(evidence, state="extracted"),
                "Validate the legacy profile findings before rebuild authorization.",
            )
        return Finding(
            "workspace.layout",
            "workspace",
            STATUS_FAIL,
            "Workspace is partial or contains an invalid extracted-tree root.",
            dict(evidence, state="partial-or-invalid"),
            "Recover or clean the workspace through the accepted transaction boundary before continuing.",
        )

    @staticmethod
    def _inspect_workspace_confinement(ctx, paths):
        guarded = (
            paths["work"],
            paths["filesystem"],
            paths["iso_tree"],
            paths["mount"],
            paths["runtime"],
        )
        syntax_issues = {
            path: _path_syntax_issues(path)
            for path in guarded
            if _path_syntax_issues(path)
        }
        evidence = {
            "filesystem_root": paths["filesystem"],
            "iso_tree_root": paths["iso_tree"],
            "mount_root": paths["mount"],
            "runtime_root": paths["runtime"],
            "source_iso": paths["source_iso"] or None,
            "workspace_root": paths["work"],
        }
        if syntax_issues:
            evidence["path_syntax_issues"] = syntax_issues
            return Finding(
                "workspace.confinement",
                "workspace",
                STATUS_FAIL,
                "Workspace path relationships cannot satisfy confinement because a guarded path is invalid.",
                evidence,
                "Use absolute normalized guarded paths, then repeat relationship checks.",
            )

        work = paths["work"]
        filesystem = paths["filesystem"]
        iso_tree = paths["iso_tree"]
        mount_root = paths["mount"]
        runtime = paths["runtime"]
        real_work = os.path.realpath(work)
        real_mount = os.path.realpath(mount_root)
        real_runtime = os.path.realpath(runtime)
        issues = []
        try:
            accepted_work, accepted_mount = mounts.validate_extract_layout(
                ctx
            )
            evidence["accepted_extract_layout"] = {
                "accepted": True,
                "mount_root": accepted_mount,
                "workspace_root": accepted_work,
            }
        except mounts.MountEvidenceError as error:
            evidence["accepted_extract_layout"] = dict(
                _error_evidence(error),
                accepted=False,
            )
            issues.append("accepted-extract-layout-rejected")
        if filesystem != os.path.join(work, "FileSystem"):
            issues.append("filesystem-is-not-the-exact-workspace-child")
        if iso_tree != os.path.join(work, "ISO"):
            issues.append("iso-tree-is-not-the-exact-workspace-child")
        if (
            _path_within(real_work, real_mount)
            or _path_within(real_mount, real_work)
        ):
            issues.append("mount-root-overlaps-workspace")
        if _path_within(real_work, real_runtime):
            issues.append("runtime-root-is-inside-workspace")
        source = paths["source_iso"]
        if (
            source
            and not _path_syntax_issues(source)
            and _path_within(real_work, os.path.realpath(source))
        ):
            issues.append("source-iso-is-inside-workspace")
        evidence.update(
            {
                "issues": issues,
                "real_mount_root": real_mount,
                "real_runtime_root": real_runtime,
                "real_workspace_root": real_work,
            }
        )
        if issues:
            return Finding(
                "workspace.confinement",
                "workspace",
                STATUS_FAIL,
                "Workspace, mount, runtime, or source paths overlap an unsafe custody boundary.",
                evidence,
                "Separate the guarded roots and keep the selected source ISO outside the purgeable workspace.",
            )
        return Finding(
            "workspace.confinement",
            "workspace",
            STATUS_PASS,
            "Workspace, mount, runtime, and source paths satisfy the accepted separation rules.",
            evidence,
            "No path-separation action is required.",
        )

    def _inspect_mount_root(self, path):
        finding = self._inspect_path(
            "workspace.mount-root",
            "workspace",
            path,
            expected_kind="directory",
            expected_owner=0,
            require_owner=False,
            require_writable=False,
            missing_status=STATUS_SKIPPED,
        )
        if finding.status != STATUS_SKIPPED:
            return finding
        return Finding(
            finding.check_id,
            finding.subsystem,
            STATUS_WARNING,
            "Mount root is absent and would require authorized creation.",
            finding.evidence,
            "Create the mount root only as part of the separately authorized extraction operation.",
        )

    def _inspect_runtime_root(self, path, owner_uid):
        syntax_issues = _path_syntax_issues(path)
        evidence = {
            "expected_mode": "0700",
            "expected_owner_uid": owner_uid,
            "path": path,
        }
        if syntax_issues:
            evidence["issues"] = syntax_issues
            return Finding(
                "workspace.runtime-root",
                "workspace",
                STATUS_FAIL,
                "Runtime custody path syntax is invalid.",
                evidence,
                "Use one absolute, normalized runtime leaf outside the workspace.",
            )
        work_issues = []
        parent = os.path.dirname(path)
        try:
            state = os.lstat(path)
        except FileNotFoundError:
            parent_issues = _runtime_parent_issues(parent)
            chain_evidence = None
            if not parent_issues:
                chain_evidence, chain_issues = _accepted_literal_chain(parent)
                parent_issues.extend(chain_issues)
            try:
                parent_state = os.lstat(parent)
                if not stat.S_ISDIR(parent_state.st_mode):
                    parent_issues.append("runtime-parent-is-not-directory")
            except OSError as error:
                parent_issues.append(
                    "runtime-parent-unavailable:{}".format(
                        type(error).__name__
                    )
                )
            evidence.update(
                {
                    "exists": False,
                    "issues": parent_issues,
                    "literal_directory_chain": chain_evidence,
                    "parent": parent,
                }
            )
            if parent_issues:
                return Finding(
                    "workspace.runtime-root",
                    "workspace",
                    STATUS_FAIL,
                    "Runtime leaf is absent and its parent chain is unsafe.",
                    evidence,
                    "Provide an existing literal and safely owned runtime parent before operation authorization.",
                )
            return Finding(
                "workspace.runtime-root",
                "workspace",
                STATUS_PASS,
                "Runtime leaf is absent but its literal parent is available.",
                evidence,
                "Allow the accepted runtime transaction to create the private leaf only when operation authority is granted.",
            )
        except OSError as error:
            evidence.update(_error_evidence(error))
            return Finding(
                "workspace.runtime-root",
                "workspace",
                STATUS_UNKNOWN,
                "Runtime custody identity could not be observed.",
                evidence,
                "Restore metadata visibility and repeat preflight.",
            )
        chain_evidence, chain_issues = _accepted_literal_chain(path)
        work_issues.extend(_literal_chain_issues(path))
        work_issues.extend(chain_issues)
        if not stat.S_ISDIR(state.st_mode):
            work_issues.append("runtime-root-is-not-directory")
        if os.path.realpath(path) != path:
            work_issues.append("runtime-root-is-aliased")
        if state.st_uid != owner_uid:
            work_issues.append("owner-mismatch")
        if stat.S_IMODE(state.st_mode) != 0o700:
            work_issues.append("mode-is-not-0700")
        evidence.update(
            {
                "exists": True,
                "issues": work_issues,
                "literal_directory_chain": chain_evidence,
                "node": _state_record(state),
            }
        )
        if work_issues:
            return Finding(
                "workspace.runtime-root",
                "workspace",
                STATUS_FAIL,
                "Runtime custody does not match the accepted private-leaf contract.",
                evidence,
                "Correct runtime path identity, ownership, and mode before operation authorization.",
            )
        return Finding(
            "workspace.runtime-root",
            "workspace",
            STATUS_PASS,
            "Runtime custody is a literal private directory.",
            evidence,
            "No action is required for runtime custody.",
        )

    def _inspect_source_iso(self, path):
        if path == "":
            return Finding(
                "input.source-iso",
                "input",
                STATUS_SKIPPED,
                "No source ISO is selected.",
                {"path_selected": False},
                "Select the preserved source ISO before extraction authorization.",
            )
        custody = self._inspect_path(
            "input.source-iso",
            "input",
            path,
            expected_kind="file",
            expected_owner=0,
            require_owner=False,
            require_writable=False,
            missing_status=STATUS_FAIL,
            require_single_link=True,
            include_full_identity=True,
        )
        if custody.status != STATUS_PASS:
            return custody
        evidence = dict(custody.evidence)
        evidence["path_selected"] = True
        initial_identity = evidence["initial_identity"]
        expected_identity = _file_identity_from_record(initial_identity)
        expected_accepted_identity = {
            "dev": initial_identity["device"],
            "ino": initial_identity["inode"],
            "mode": stat.S_IMODE(initial_identity["mode"]),
            "path": os.path.abspath(path),
            "size": initial_identity["size_bytes"],
        }
        try:
            accepted_before = mounts.iso_source_identity(path)
            if accepted_before != expected_accepted_identity:
                raise ValueError(
                    "Accepted ISO source identity differs from initial custody"
                )
            digest, hash_state = self._hash_regular_file(
                path,
                expected_identity=expected_identity,
            )
            accepted_after = mounts.iso_source_identity(path)
            reobserved_state = os.lstat(path)
            hashed_identity = _file_identity_record(hash_state)
            reobserved_identity = _file_identity_record(reobserved_state)
            if (
                accepted_before != accepted_after
                or accepted_after != expected_accepted_identity
                or _file_identity(hash_state) != expected_identity
                or _file_identity(reobserved_state) != expected_identity
            ):
                raise ValueError(
                    "Accepted ISO source identity changed during observation"
                )
        except Exception as error:
            evidence.update(_error_evidence(error))
            evidence["content_observed"] = False
            return Finding(
                "input.source-iso",
                "input",
                STATUS_FAIL,
                "Selected source ISO content or identity changed or could not be proven.",
                evidence,
                "Preserve the source byte-identically and repeat observation before Phase 1E-B inspection.",
            )
        evidence.update(
            {
                "accepted_source_identity": accepted_before,
                "accepted_source_identity_after": accepted_after,
                "content_observed": True,
                "hashed_identity": hashed_identity,
                "reobserved_identity": reobserved_identity,
                "sha256": digest,
            }
        )
        return Finding(
            "input.source-iso",
            "input",
            STATUS_PASS,
            "Selected source ISO has strict literal custody and descriptor-safe content evidence.",
            evidence,
            "Preserve this exact inode and SHA-256 evidence through the next bounded phase.",
        )

    def _inspect_capacity(self, path, work_finding):
        if work_finding.status != STATUS_PASS:
            observation = Finding(
                "capacity.workspace",
                "capacity",
                STATUS_SKIPPED,
                "Capacity is not measured because workspace custody failed.",
                {
                    "path": path,
                    "workspace_status": work_finding.status,
                },
                "Correct workspace custody, then repeat exact capacity observation.",
            )
            sufficiency = Finding(
                "capacity.sufficiency",
                "capacity",
                STATUS_UNKNOWN,
                "Capacity sufficiency is unresolved.",
                {
                    "available_bytes": None,
                    "requirement_bytes": None,
                    "sufficiency_evaluated": False,
                },
                "Phase 1E-B must establish a defensible requirement before evaluating sufficiency.",
            )
            return observation, sufficiency
        try:
            value = self.statvfs(path)
            fragment = int(value.f_frsize or value.f_bsize)
            evidence = {
                "available_bytes": int(value.f_bavail) * fragment,
                "free_bytes": int(value.f_bfree) * fragment,
                "path": path,
                "total_bytes": int(value.f_blocks) * fragment,
            }
        except Exception as error:
            evidence = {"path": path}
            evidence.update(_error_evidence(error))
            observation = Finding(
                "capacity.workspace",
                "capacity",
                STATUS_UNKNOWN,
                "Filesystem capacity could not be observed.",
                evidence,
                "Restore filesystem-statistics visibility and compare exact capacity before factory authorization.",
            )
            sufficiency = Finding(
                "capacity.sufficiency",
                "capacity",
                STATUS_UNKNOWN,
                "Capacity sufficiency is unresolved.",
                {
                    "available_bytes": None,
                    "requirement_bytes": None,
                    "sufficiency_evaluated": False,
                },
                "Restore capacity observation, then establish a defensible requirement in Phase 1E-B.",
            )
            return observation, sufficiency
        observation = Finding(
            "capacity.workspace",
            "capacity",
            STATUS_PASS,
            "Exact workspace filesystem capacity was observed.",
            evidence,
            "Retain the raw byte counts without interpreting readiness in Phase 1E-A.",
        )
        sufficiency = Finding(
            "capacity.sufficiency",
            "capacity",
            STATUS_UNKNOWN,
            "Capacity sufficiency is unresolved.",
            {
                "available_bytes": evidence["available_bytes"],
                "requirement_bytes": None,
                "sufficiency_evaluated": False,
            },
            "Phase 1E-B must establish a defensible requirement before evaluating sufficiency.",
        )
        return observation, sufficiency

    def _inspect_dependencies(self):
        findings = []
        for spec in self.dependencies:
            check_id = "dependency." + re.sub(
                r"[^a-z0-9.-]+",
                "-",
                spec.command.lower(),
            )
            status, evidence = self._observe_executable(spec.command)
            evidence.update(
                {
                    "purpose": spec.purpose,
                    "required": bool(spec.required),
                }
            )
            if status == STATUS_UNKNOWN:
                findings.append(
                    Finding(
                        check_id,
                        "dependency",
                        STATUS_UNKNOWN,
                        "Dependency discovery could not complete.",
                        evidence,
                        "Restore executable-path discovery and repeat preflight; do not install from this engine.",
                    )
                )
                continue
            if status == STATUS_FAIL:
                missing = evidence["discovered"] is False
                findings.append(
                    Finding(
                        check_id,
                        "dependency",
                        STATUS_FAIL if spec.required else STATUS_WARNING,
                        "Required executable is not discoverable."
                        if missing and spec.required
                        else (
                            "Optional executable is not discoverable."
                            if missing
                            else "Discovered executable custody is invalid."
                        ),
                        evidence,
                        "Provide the dependency through a separately authorized package or toolchain operation.",
                    )
                )
                continue
            findings.append(
                Finding(
                    check_id,
                    "dependency",
                    STATUS_PASS,
                    "Dependency path is discoverable; its version remains unobserved.",
                    evidence,
                    "Phase 1E-B may run the bounded version query before authorization.",
                )
            )
        return tuple(findings)

    def _inspect_source_media_readiness(self, source_finding):
        isoinfo_status, isoinfo = self._observe_executable("isoinfo")
        xorriso_status, xorriso = self._observe_executable("xorriso")
        selected = None
        selected_path = None
        if isoinfo_status == STATUS_PASS:
            selected = "isoinfo"
            selected_path = isoinfo["canonical_path"]
        elif xorriso_status == STATUS_PASS:
            selected = "xorriso"
            selected_path = xorriso["canonical_path"]

        evidence = {
            "inspection_executed": False,
            "isoinfo": isoinfo,
            "preferred_provider": "isoinfo",
            "selected_path": selected_path,
            "selected_provider": selected,
            "xorriso": xorriso,
        }
        if selected is not None:
            inspector = Finding(
                "media.source-inspector",
                "media",
                STATUS_PASS,
                "A root-free source-media inspector path is available but has not been executed.",
                evidence,
                "Phase 1E-B must execute the selected bounded inspector and parse its result.",
            )
        elif (
            isoinfo_status == STATUS_UNKNOWN
            or xorriso_status == STATUS_UNKNOWN
        ):
            inspector = Finding(
                "media.source-inspector",
                "media",
                STATUS_UNKNOWN,
                "Source-media inspector discovery could not be resolved.",
                evidence,
                "Restore executable-path visibility and repeat discovery without executing either inspector.",
            )
        else:
            inspector = Finding(
                "media.source-inspector",
                "media",
                STATUS_FAIL,
                "Neither accepted root-free source-media inspector is available.",
                evidence,
                "Provide isoinfo or xorriso through a separately authorized package operation.",
            )

        profile = Finding(
            "media.source-profile",
            "media",
            STATUS_UNKNOWN,
            "The selected source-media profile remains unresolved until bounded inspection.",
            {
                "inspection_executed": False,
                "inspection_provider": selected,
                "profile_result": None,
                "source_status": source_finding.status,
            },
            "Phase 1E-B must inspect the preserved source and record the bounded parser result.",
        )
        return inspector, profile

    def _inspect_qemu_readiness(self):
        selection_rule = (
            "x86_64 selects qemu-system-x86_64; "
            "i386, i486, i586, and i686 select qemu-system-i386"
        )
        try:
            machine = self.machine_reader()
            if not isinstance(machine, str) or not machine:
                raise ValueError("Machine architecture is invalid")
        except Exception as error:
            qemu_status = STATUS_UNKNOWN
            qemu_evidence = {
                "architecture_supported": None,
                "command": None,
                "executable_lookup_executed": False,
                "host_machine": None,
                "selection_rule": selection_rule,
                "version_query_executed": False,
                "version_status": "unobserved-until-phase-1e-b",
            }
            qemu_evidence.update(_error_evidence(error))
        else:
            if machine == "x86_64":
                qemu_command = "qemu-system-x86_64"
            elif re.fullmatch(r"i[3-6]86", machine):
                qemu_command = "qemu-system-i386"
            else:
                qemu_command = None

            if qemu_command is None:
                qemu_status = STATUS_UNKNOWN
                qemu_evidence = {
                    "architecture_supported": False,
                    "command": None,
                    "executable_lookup_executed": False,
                    "host_machine": machine,
                    "selection_rule": selection_rule,
                    "version_query_executed": False,
                    "version_status": "deferred-unsupported-architecture",
                }
            else:
                qemu_status, qemu_evidence = self._observe_executable(
                    qemu_command
                )
                qemu_evidence.update(
                    {
                        "architecture_supported": True,
                        "executable_lookup_executed": True,
                        "host_machine": machine,
                        "selection_rule": selection_rule,
                    }
                )

        if qemu_status == STATUS_PASS:
            qemu_binary = Finding(
                "qemu.binary",
                "qemu",
                STATUS_PASS,
                "The architecture-appropriate QEMU path is discoverable; its version is unobserved.",
                qemu_evidence,
                "Phase 1E-B may execute a bounded version query before any boot test.",
            )
        elif qemu_status == STATUS_FAIL:
            qemu_binary = Finding(
                "qemu.binary",
                "qemu",
                STATUS_FAIL,
                "The architecture-appropriate QEMU executable is unavailable or invalid.",
                qemu_evidence,
                "Provide the selected QEMU binary through a separately authorized package operation.",
            )
        else:
            unsupported_architecture = (
                qemu_evidence.get("architecture_supported") is False
            )
            qemu_binary = Finding(
                "qemu.binary",
                "qemu",
                STATUS_UNKNOWN,
                (
                    "QEMU binary selection is deferred because the host architecture is unsupported."
                    if unsupported_architecture
                    else "QEMU architecture or executable discovery could not complete."
                ),
                qemu_evidence,
                (
                    "Use an explicitly supported x86_64 or i386-family host before Phase 1E-B QEMU planning."
                    if unsupported_architecture
                    else "Restore architecture and executable-path observation before Phase 1E-B."
                ),
            )

        kvm_evidence = {
            "acceleration_fallback": "tcg",
            "kvm_required": False,
            "path": self.kvm_path,
        }
        if _path_syntax_issues(self.kvm_path):
            kvm_evidence["issues"] = _path_syntax_issues(self.kvm_path)
            kvm = Finding(
                "qemu.kvm",
                "qemu",
                STATUS_UNKNOWN,
                "KVM device path syntax is invalid.",
                kvm_evidence,
                "Use the literal /dev/kvm observation path; TCG remains the accepted fallback.",
            )
        else:
            try:
                kvm_state = self.kvm_state_reader(self.kvm_path)
            except FileNotFoundError:
                kvm_evidence.update(
                    {
                        "exists": False,
                        "read_write_access": False,
                    }
                )
                kvm = Finding(
                    "qemu.kvm",
                    "qemu",
                    STATUS_WARNING,
                    "KVM acceleration is absent; TCG remains available.",
                    kvm_evidence,
                    "Allow a longer Phase 1E-B BIOS CD-ROM test budget when using TCG.",
                )
            except OSError as error:
                kvm_evidence.update(_error_evidence(error))
                kvm = Finding(
                    "qemu.kvm",
                    "qemu",
                    STATUS_UNKNOWN,
                    "KVM device state could not be observed.",
                    kvm_evidence,
                    "Restore device metadata visibility or plan to use TCG without claiming KVM.",
                )
            else:
                kind = _node_kind(kvm_state.st_mode)
                try:
                    accessible = bool(
                        self.access(
                            self.kvm_path,
                            os.R_OK | os.W_OK,
                        )
                    )
                except Exception as error:
                    kvm_evidence.update(
                        {
                            "exists": True,
                            "node": _state_record(kvm_state),
                            "read_write_access": None,
                        }
                    )
                    kvm_evidence.update(_error_evidence(error))
                    kvm = Finding(
                        "qemu.kvm",
                        "qemu",
                        STATUS_UNKNOWN,
                        "KVM accessibility could not be observed.",
                        kvm_evidence,
                        "Restore access-observation visibility or plan to use TCG without claiming KVM.",
                    )
                    accessible = None
                kvm_evidence.update(
                    {
                        "exists": True,
                        "node": _state_record(kvm_state),
                        "read_write_access": accessible,
                    }
                )
                if accessible is None:
                    pass
                elif kind == "character-device" and accessible:
                    kvm = Finding(
                        "qemu.kvm",
                        "qemu",
                        STATUS_PASS,
                        "KVM is a character device accessible for reading and writing.",
                        kvm_evidence,
                        "Treat KVM only as an acceleration observation; it grants no boot authority.",
                    )
                else:
                    kvm = Finding(
                        "qemu.kvm",
                        "qemu",
                        STATUS_WARNING,
                        "KVM acceleration is unusable; TCG remains available.",
                        kvm_evidence,
                        "Use TCG or correct KVM custody separately without blocking BIOS CD-ROM planning.",
                    )

        contract = Finding(
            "qemu.acceptance-contract",
            "qemu",
            STATUS_UNKNOWN,
            "The accepted QEMU boot test remains unexecuted.",
            {
                "accepted_boot_path": "bios-cdrom",
                "boot_executed": False,
                "excluded": (
                    "uefi",
                    "ovmf",
                    "usb-emulation",
                ),
                "exact_argv_captured": False,
            },
            "Phase 1E-B owns bounded BIOS -cdrom planning; excluded boot paths remain outside scope.",
        )
        return qemu_binary, kvm, contract

    def _inspect_machine_resources(self):
        cpu_count = None
        try:
            cpu_count = self.cpu_count_reader()
            if type(cpu_count) is not int or cpu_count < 1:
                raise ValueError("Logical CPU count is invalid")
            cpu = Finding(
                "resource.cpu",
                "resource",
                STATUS_PASS,
                "Logical CPU count was observed without a readiness threshold.",
                {
                    "logical_cpu_count": cpu_count,
                    "threshold": None,
                },
                "Retain this raw fact for Phase 1E-B scheduling without converting it into authorization.",
            )
        except Exception as error:
            evidence = {"logical_cpu_count": None, "threshold": None}
            evidence.update(_error_evidence(error))
            cpu = Finding(
                "resource.cpu",
                "resource",
                STATUS_UNKNOWN,
                "Logical CPU count could not be observed.",
                evidence,
                "Restore CPU topology visibility and repeat observation without inventing a threshold.",
            )

        try:
            load = tuple(float(value) for value in self.loadavg_reader())
            if (
                len(load) != 3
                or any(
                    not math.isfinite(value) or value < 0
                    for value in load
                )
            ):
                raise ValueError("Load averages are invalid")
            ratios = (
                [value / cpu_count for value in load]
                if cpu_count is not None
                else [None, None, None]
            )
            load_finding = Finding(
                "resource.load",
                "resource",
                STATUS_PASS,
                "Load averages and raw CPU ratios were observed without a readiness decision.",
                {
                    "load_1m": load[0],
                    "load_5m": load[1],
                    "load_15m": load[2],
                    "load_per_cpu_1m": ratios[0],
                    "load_per_cpu_5m": ratios[1],
                    "load_per_cpu_15m": ratios[2],
                    "threshold": None,
                },
                "Use these raw values only for scheduling; Phase 1E-A grants no factory authority.",
            )
        except Exception as error:
            evidence = {"threshold": None}
            evidence.update(_error_evidence(error))
            load_finding = Finding(
                "resource.load",
                "resource",
                STATUS_UNKNOWN,
                "Load averages could not be observed.",
                evidence,
                "Restore load-average visibility and repeat observation without a readiness threshold.",
            )

        try:
            memory_evidence = _parse_meminfo(self.meminfo_reader())
            memory_evidence["threshold"] = None
            memory = Finding(
                "resource.memory",
                "resource",
                STATUS_PASS,
                "Memory and swap facts were observed without a readiness decision.",
                memory_evidence,
                "Use raw memory and swap facts only for Phase 1E-B scheduling.",
            )
        except Exception as error:
            evidence = {"threshold": None}
            evidence.update(_error_evidence(error))
            memory = Finding(
                "resource.memory",
                "resource",
                STATUS_UNKNOWN,
                "Memory and swap facts could not be observed.",
                evidence,
                "Restore /proc memory visibility and repeat observation without a threshold.",
            )
        return cpu, load_finding, memory

    @staticmethod
    def _inspect_operation_plan():
        return Finding(
            "factory.operation-plan",
            "factory",
            STATUS_UNKNOWN,
            "The accepted factory stage order is known, but capability inputs and exact argv remain unresolved.",
            {
                "commands_executed": 0,
                "exact_argv_captured": False,
                "factory_authority_granted": False,
                "ordered_stages": _FACTORY_STAGES,
                "phase_1e_b_responsibility": (
                    "bounded capability probes",
                    "bounded source inspection",
                    "version queries",
                    "exact executable argv capture",
                    "capacity requirement",
                    "authorization handoff",
                ),
                "unresolved_inputs": (
                    "dependency-versions",
                    "squashfs-compressor-capability",
                    "source-media-profile",
                    "capacity-sufficiency",
                    "qemu-version",
                ),
            },
            "Phase 1E-B must resolve capabilities and capture exact argv without duplicating rebuild command construction here.",
        )

    def _inspect_mounts(self, paths):
        try:
            identities = tuple(self.mountinfo_reader())
            if any(
                not isinstance(identity, mounts.MountIdentity)
                for identity in identities
            ):
                raise TypeError("Mount reader returned an invalid identity")
            mount_ids = [identity.mount_id for identity in identities]
            if len(mount_ids) != len(set(mount_ids)):
                raise ValueError("Mount identifiers are duplicated")
        except Exception as error:
            evidence = {"scope": "workspace-and-mount-root"}
            evidence.update(_error_evidence(error))
            return Finding(
                "mount.workspace",
                "mount",
                STATUS_UNKNOWN,
                "Exact mount evidence could not be observed.",
                evidence,
                "Restore readable mountinfo evidence and repeat preflight before any cleanup or factory operation.",
            )
        selected = {}
        work_path = paths["work"]
        mount_path = paths["mount"]
        if not _path_syntax_issues(work_path):
            for identity in mounts.mounts_under(
                identities,
                work_path,
                include_root=True,
            ):
                selected[identity.key] = identity
        if not _path_syntax_issues(mount_path):
            for identity in mounts.mounts_under(
                identities,
                mount_path,
                include_root=True,
            ):
                selected[identity.key] = identity
        records = [
            {
                "fs_type": identity.fs_type,
                "mount_id": identity.mount_id,
                "mount_point": identity.mount_point,
                "source": identity.source,
            }
            for identity in sorted(
                selected.values(),
                key=lambda item: (item.mount_point, item.mount_id),
            )
        ]
        evidence = {
            "active_mount_count": len(records),
            "active_mounts": records,
            "mount_root": mount_path,
            "workspace_root": work_path,
        }
        if records:
            return Finding(
                "mount.workspace",
                "mount",
                STATUS_FAIL,
                "Active mounts overlap the workspace or configured mount root.",
                evidence,
                "Use accepted mount-session recovery and verify exact identities before factory authorization.",
            )
        return Finding(
            "mount.workspace",
            "mount",
            STATUS_PASS,
            "No active mount identity overlaps the guarded paths.",
            evidence,
            "No mount recovery action is required.",
        )

    def _read_lock_records(self):
        try:
            return _parse_lock_text(self.lock_text_reader()), None
        except Exception as error:
            return tuple(), error

    @staticmethod
    def _inspect_lock(
        check_id,
        path,
        owner_uid,
        lock_records,
        lock_error,
        persistent,
    ):
        evidence = {
            "expected_mode": "0600",
            "expected_owner_uid": owner_uid,
            "path": path,
        }
        if _path_syntax_issues(path):
            evidence["issues"] = _path_syntax_issues(path)
            return Finding(
                check_id,
                "lock",
                STATUS_FAIL,
                "Lock path syntax is invalid.",
                evidence,
                "Correct the configured custody paths before operation authorization.",
            )
        try:
            state = os.lstat(path)
        except FileNotFoundError:
            evidence.update({"exists": False, "held": False})
            return Finding(
                check_id,
                "lock",
                STATUS_PASS,
                "No lock inode exists at this custody path.",
                evidence,
                "No lock recovery action is required.",
            )
        except OSError as error:
            evidence.update(_error_evidence(error))
            return Finding(
                check_id,
                "lock",
                STATUS_UNKNOWN,
                "Lock inode could not be observed.",
                evidence,
                "Restore lock metadata visibility and repeat preflight.",
            )
        issues = []
        issues.extend(_literal_chain_issues(path))
        if not stat.S_ISREG(state.st_mode):
            issues.append("lock-is-not-regular")
        if stat.S_IMODE(state.st_mode) != 0o600:
            issues.append("mode-is-not-0600")
        if state.st_uid != owner_uid:
            issues.append("owner-mismatch")
        if state.st_nlink != 1:
            issues.append("link-count-is-not-one")
        if not persistent and not issues:
            metadata, metadata_issues = _read_chroot_lock_metadata(
                path,
                state,
            )
            evidence["metadata"] = metadata
            issues.extend(metadata_issues)
        evidence.update(
            {
                "exists": True,
                "issues": issues,
                "node": _state_record(state),
            }
        )
        if issues:
            return Finding(
                check_id,
                "lock",
                STATUS_FAIL,
                "Lock inode custody is unsafe.",
                evidence,
                "Preserve the inode as evidence and obtain an explicit recovery decision before mutation.",
            )
        if lock_error is not None:
            evidence.update(_error_evidence(lock_error))
            return Finding(
                check_id,
                "lock",
                STATUS_UNKNOWN,
                "Lock inode is valid but kernel ownership is unknown.",
                evidence,
                "Restore readable kernel lock evidence and repeat preflight.",
            )
        holders = [
            {
                "kind": record["kind"],
                "mode": record["mode"],
                "pid": record["pid"],
            }
            for record in lock_records
            if (
                record["device_major"] == os.major(state.st_dev)
                and record["device_minor"] == os.minor(state.st_dev)
                and record["inode"] == state.st_ino
            )
        ]
        evidence.update(
            {
                "held": bool(holders),
                "holder_count": len(holders),
                "holders": holders,
            }
        )
        if holders:
            return Finding(
                check_id,
                "lock",
                STATUS_FAIL,
                "A live kernel lock holder owns this operation boundary.",
                evidence,
                "Allow the owning operation to finish or obtain explicit recovery authority; do not remove the lock inode.",
            )
        if not persistent:
            return Finding(
                check_id,
                "lock",
                STATUS_WARNING,
                "An unlocked chroot transaction lock requires stale-state recovery.",
                evidence,
                "Use the accepted chroot transaction recovery path before factory work.",
            )
        return Finding(
            check_id,
            "lock",
            STATUS_PASS,
            "Managed lock infrastructure exists without a live holder.",
            evidence,
            "No lock recovery action is required.",
        )

    @staticmethod
    def _inspect_journal(
        check_id,
        path,
        pending_marker,
        owner_uid,
        expected_roots,
        lock_finding,
    ):
        evidence = {"path": path}
        syntax_issues = _path_syntax_issues(path)
        parent_issues = _literal_chain_issues(os.path.dirname(path))
        unsafe_parent_issues = [
            issue
            for issue in parent_issues
            if not issue.startswith("missing-ancestor:")
        ]
        if syntax_issues or unsafe_parent_issues:
            evidence["issues"] = syntax_issues + unsafe_parent_issues
            return Finding(
                check_id,
                "journal",
                STATUS_FAIL,
                "Journal path custody is invalid.",
                evidence,
                "Correct the literal journal path without following aliases, then repeat preflight.",
            )
        try:
            pending_paths = _pending_journal_paths(path, pending_marker)
        except OSError as error:
            evidence.update(_error_evidence(error))
            return Finding(
                check_id,
                "journal",
                STATUS_UNKNOWN,
                "Pending journal namespace could not be observed.",
                evidence,
                "Restore journal-directory visibility and repeat preflight.",
            )
        evidence["pending_count"] = len(pending_paths)
        if pending_paths:
            evidence["pending_evidence_preserved"] = True
        try:
            state = os.lstat(path)
        except FileNotFoundError:
            evidence["exists"] = False
            if pending_paths:
                return Finding(
                    check_id,
                    "journal",
                    STATUS_FAIL,
                    "Pending journal evidence exists without a final journal.",
                    evidence,
                    "Preserve all pending evidence and obtain accepted recovery analysis before mutation.",
                )
            return Finding(
                check_id,
                "journal",
                STATUS_PASS,
                "No transaction journal evidence exists.",
                evidence,
                "No journal recovery action is required.",
            )
        except OSError as error:
            evidence.update(_error_evidence(error))
            return Finding(
                check_id,
                "journal",
                STATUS_UNKNOWN,
                "Journal identity could not be observed.",
                evidence,
                "Restore journal metadata visibility and repeat preflight.",
            )
        issues = []
        issues.extend(_literal_chain_issues(path))
        if not stat.S_ISREG(state.st_mode):
            issues.append("journal-is-not-regular")
        if stat.S_IMODE(state.st_mode) != 0o600:
            issues.append("mode-is-not-0600")
        if state.st_uid != owner_uid:
            issues.append("owner-mismatch")
        if state.st_nlink != 1:
            issues.append("link-count-is-not-one")
        if state.st_size > _MAX_JOURNAL_BYTES:
            issues.append("journal-exceeds-observation-limit")
        evidence.update(
            {
                "exists": True,
                "issues": issues,
                "node": _state_record(state),
            }
        )
        if pending_paths:
            issues.append("pending-journal-evidence-exists")
        if not lock_finding.evidence.get("exists", False):
            issues.append("journal-exists-without-lock-inode")
        elif lock_finding.evidence.get("issues"):
            issues.append("journal-lock-custody-is-invalid")
        if issues:
            return Finding(
                check_id,
                "journal",
                STATUS_FAIL,
                "Journal custody is unsafe or interrupted.",
                evidence,
                "Preserve all journal evidence and obtain accepted recovery analysis before mutation.",
            )
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            try:
                descriptor_state = os.fstat(descriptor)
                if (
                    descriptor_state.st_dev != state.st_dev
                    or descriptor_state.st_ino != state.st_ino
                    or descriptor_state.st_size != state.st_size
                ):
                    raise ValueError("Journal identity changed during observation")
                raw = os.read(descriptor, _MAX_JOURNAL_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > _MAX_JOURNAL_BYTES:
                raise ValueError("Journal exceeds the observation limit")
            value = json.loads(raw.decode("utf-8"))
            metadata, schema_issues = _journal_summary(
                check_id,
                value,
                expected_roots,
            )
            evidence["metadata"] = metadata
            if schema_issues:
                evidence["schema_issues"] = schema_issues
                return Finding(
                    check_id,
                    "journal",
                    STATUS_FAIL,
                    "Journal content does not match the accepted recovery schema.",
                    evidence,
                    "Preserve the journal byte-identically and obtain accepted recovery analysis before mutation.",
                )
        except Exception as error:
            evidence.update(_error_evidence(error))
            return Finding(
                check_id,
                "journal",
                STATUS_FAIL,
                "Journal content is corrupt or changed during observation.",
                evidence,
                "Preserve the journal byte-identically and obtain accepted recovery analysis before mutation.",
            )
        return Finding(
            check_id,
            "journal",
            STATUS_WARNING,
            "A structurally readable recovery journal is present.",
            evidence,
            "Run the accepted recovery path under separate operation authority before factory work.",
        )

    @staticmethod
    def _inspect_legacy_profile(
        filesystem_root,
        iso_root,
        filesystem_finding,
        iso_finding,
    ):
        fs_exists = bool(filesystem_finding.evidence.get("exists"))
        iso_exists = bool(iso_finding.evidence.get("exists"))
        if not fs_exists and not iso_exists:
            return Finding(
                "media.legacy-extracted-profile",
                "media",
                STATUS_SKIPPED,
                "Legacy profile recognition is skipped for an empty workspace.",
                {
                    "filesystem_exists": False,
                    "iso_tree_exists": False,
                    "profile": None,
                    "recognized": False,
                },
                "Extract the known legacy source through the accepted operation before profile recognition.",
            )
        requirements = (
            (filesystem_root, "", "directory"),
            (filesystem_root, "etc", "directory"),
            (filesystem_root, "usr", "directory"),
            (filesystem_root, "root", "directory"),
            (iso_root, "", "directory"),
            (iso_root, "isolinux", "directory"),
            (iso_root, "isolinux/isolinux.bin", "file"),
            (iso_root, "casper", "directory"),
            (iso_root, ".disk", "directory"),
        )
        records = []
        valid = True
        for root, relative, expected_kind in requirements:
            path = root if not relative else os.path.join(root, relative)
            record = {
                "expected_kind": expected_kind,
                "relative_path": relative or ".",
                "root": "FileSystem" if root == filesystem_root else "ISO",
            }
            try:
                state = os.lstat(path)
                issues = _literal_chain_issues(path)
                if _node_kind(state.st_mode) != expected_kind:
                    issues.append("unexpected-kind")
                if os.path.realpath(path) != path:
                    issues.append("canonical-path-mismatch")
                if expected_kind == "file" and state.st_nlink != 1:
                    issues.append("link-count-is-not-one")
                record.update(
                    {
                        "exists": True,
                        "issues": issues,
                        "node": _state_record(state),
                    }
                )
                if issues:
                    valid = False
            except FileNotFoundError:
                record.update({"exists": False, "issues": ["missing"]})
                valid = False
            except OSError as error:
                record.update(
                    {
                        "exists": None,
                        "issues": ["unreadable"],
                    }
                )
                record.update(_error_evidence(error))
                valid = False
            records.append(record)
        evidence = {
            "profile": "legacy-isolinux-single-filesystem-extracted-tree"
            if valid
            else None,
            "recognized": valid,
            "required_nodes": records,
        }
        if valid:
            return Finding(
                "media.legacy-extracted-profile",
                "media",
                STATUS_PASS,
                "Extracted workspace matches the accepted legacy-media profile.",
                evidence,
                "No media-profile action is required before legacy rebuild planning.",
            )
        return Finding(
            "media.legacy-extracted-profile",
            "media",
            STATUS_FAIL,
            "Extracted workspace does not match the accepted legacy-media profile.",
            evidence,
            "Use the known legacy source or defer the media to a separately designed modern-media lane.",
        )

    @staticmethod
    def _hash_regular_file(path, expected_identity=None):
        state = os.lstat(path)
        if (
            expected_identity is not None
            and _file_identity(state) != expected_identity
        ):
            raise ValueError(
                "Publication artifact differs from initial custody"
            )
        if (
            not stat.S_ISREG(state.st_mode)
            or stat.S_ISLNK(state.st_mode)
            or state.st_nlink != 1
        ):
            raise ValueError("Publication artifact is not a single-link regular file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if _file_identity(opened) != _file_identity(state):
                raise ValueError("Publication artifact identity changed")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            final_state = os.fstat(descriptor)
            path_state = os.lstat(path)
            if (
                _file_identity(final_state) != _file_identity(opened)
                or _file_identity(path_state) != _file_identity(opened)
            ):
                raise ValueError("Publication artifact changed while hashing")
        finally:
            os.close(descriptor)
        return digest.hexdigest(), state

    @staticmethod
    def _read_sidecar(path):
        state = os.lstat(path)
        if (
            not stat.S_ISREG(state.st_mode)
            or stat.S_ISLNK(state.st_mode)
            or state.st_nlink != 1
            or state.st_size > _MAX_SIDECAR_BYTES
        ):
            raise ValueError("Publication sidecar custody is invalid")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if _file_identity(opened) != _file_identity(state):
                raise ValueError("Publication sidecar identity changed")
            raw = os.read(descriptor, _MAX_SIDECAR_BYTES + 1)
            final_state = os.fstat(descriptor)
            path_state = os.lstat(path)
            if (
                _file_identity(final_state) != _file_identity(opened)
                or _file_identity(path_state) != _file_identity(opened)
            ):
                raise ValueError(
                    "Publication sidecar changed during observation"
                )
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_SIDECAR_BYTES:
            raise ValueError("Publication sidecar exceeds the observation limit")
        return raw, state

    @classmethod
    def _inspect_prior_publication(
        cls,
        work_root,
        source_iso,
        work_finding,
        owner_uid,
    ):
        if work_finding.status != STATUS_PASS:
            return Finding(
                "publication.prior-pair",
                "publication",
                STATUS_SKIPPED,
                "Prior publication state is skipped because workspace custody failed.",
                {
                    "pair_count": None,
                    "workspace_status": work_finding.status,
                },
                "Correct workspace custody and repeat publication observation.",
            )
        try:
            entries = tuple(os.scandir(work_root))
        except OSError as error:
            evidence = {"path": work_root}
            evidence.update(_error_evidence(error))
            return Finding(
                "publication.prior-pair",
                "publication",
                STATUS_UNKNOWN,
                "Prior publication directory could not be listed.",
                evidence,
                "Restore directory visibility and repeat preflight.",
            )
        names = {entry.name for entry in entries}
        residue = sorted(
            name
            for name in names
            if name.startswith(".liveusb-publish-")
        )
        source_absolute = (
            os.path.abspath(source_iso)
            if source_iso and not _path_syntax_issues(source_iso)
            else None
        )
        iso_names = sorted(
            name
            for name in names
            if name.endswith(".iso")
            and os.path.join(work_root, name) != source_absolute
            and not name.startswith(".liveusb-publish-")
        )
        sidecar_names = sorted(
            name
            for name in names
            if name.endswith(".sha256")
            and not name.startswith(".liveusb-publish-")
        )
        stems = sorted(
            set(name[:-4] for name in iso_names)
            | set(name[:-7] for name in sidecar_names)
        )
        valid_pairs = []
        invalid_pairs = []
        for stem in stems:
            iso_name = stem + ".iso"
            sidecar_name = stem + ".sha256"
            pair = {
                "iso": iso_name,
                "sidecar": sidecar_name,
            }
            if iso_name not in names or sidecar_name not in names:
                pair["issue"] = "orphaned-pair"
                invalid_pairs.append(pair)
                continue
            iso_path = os.path.join(work_root, iso_name)
            sidecar_path = os.path.join(work_root, sidecar_name)
            try:
                accepted_digest = rebuild._validate_prior_pair(
                    iso_path,
                    sidecar_path,
                )
                digest, iso_state = cls._hash_regular_file(iso_path)
                raw, sidecar_state = cls._read_sidecar(sidecar_path)
                expected = rebuild._sidecar_payload(digest, iso_path)
                issues = []
                if accepted_digest != digest:
                    issues.append("accepted-validator-digest-mismatch")
                if raw != expected:
                    issues.append("sidecar-mismatch")
                if stat.S_IMODE(iso_state.st_mode) != 0o555:
                    issues.append("iso-mode-is-not-0555")
                if iso_state.st_uid != owner_uid:
                    issues.append("iso-owner-mismatch")
                if sidecar_state.st_uid != owner_uid:
                    issues.append("sidecar-owner-mismatch")
                pair.update(
                    {
                        "iso_node": _state_record(iso_state),
                        "accepted_publication_semantics": True,
                        "sha256": digest,
                        "sidecar_node": _state_record(sidecar_state),
                    }
                )
                if issues:
                    pair["issues"] = issues
                    invalid_pairs.append(pair)
                else:
                    valid_pairs.append(pair)
            except Exception as error:
                pair.update(_error_evidence(error))
                pair["issue"] = "unsafe-or-unreadable-pair"
                invalid_pairs.append(pair)
        evidence = {
            "invalid_pairs": invalid_pairs,
            "pair_count": len(valid_pairs) + len(invalid_pairs),
            "publication_residue_count": len(residue),
            "publication_residue_present": bool(residue),
            "valid_pairs": valid_pairs,
        }
        if residue or invalid_pairs:
            return Finding(
                "publication.prior-pair",
                "publication",
                STATUS_FAIL,
                "Prior publication state contains unsafe, partial, or transaction-owned evidence.",
                evidence,
                "Preserve all evidence and use accepted publication recovery before creating another final pair.",
            )
        if len(valid_pairs) > 1:
            return Finding(
                "publication.prior-pair",
                "publication",
                STATUS_WARNING,
                "Multiple valid prior publication pairs are present.",
                evidence,
                "Select the intended prior product explicitly before factory authorization.",
            )
        if valid_pairs:
            return Finding(
                "publication.prior-pair",
                "publication",
                STATUS_PASS,
                "One valid prior ISO and SHA-256 pair is present.",
                evidence,
                "Preserve the pair through the accepted crash-durable publication boundary.",
            )
        return Finding(
            "publication.prior-pair",
            "publication",
            STATUS_PASS,
            "No prior final ISO publication pair is present.",
            evidence,
            "No prior-publication recovery action is required.",
        )


def run_preflight(ctx, **engine_options):
    """Return one observation-only report for *ctx*."""

    return PreflightEngine(**engine_options).inspect(ctx)
