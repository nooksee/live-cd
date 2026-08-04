"""Bounded runtime evidence for the observation-only factory preflight."""

from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from . import mounts
from . import preflight


SCHEMA_VERSION = "liveusb.preflight-runtime.v1"
STATUS_ABSENT = "absent"
STATUS_SUCCESS = "success"
STATUS_NONZERO = "nonzero"
STATUS_TIMEOUT = "timeout"
STATUS_MALFORMED = "malformed-output"
STATUS_EXECUTION_ERROR = "execution-error"
STATUS_CUSTODY_FAILURE = "custody-failure"
STATUS_PROFILE_REJECTED = "profile-rejected"
RESULT_STATUSES = (
    STATUS_ABSENT,
    STATUS_SUCCESS,
    STATUS_NONZERO,
    STATUS_TIMEOUT,
    STATUS_MALFORMED,
    STATUS_EXECUTION_ERROR,
    STATUS_CUSTODY_FAILURE,
    STATUS_PROFILE_REJECTED,
)

DEFAULT_TIMEOUT_SECONDS = 8.0
VERSION_OUTPUT_LIMIT_BYTES = 256 * 1024
INSPECTION_OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_STOP_GRACE_SECONDS = 0.25
_PROBE_ENVIRONMENT = {
    "LANG": "C",
    "LANGUAGE": "C",
    "LC_ALL": "C",
    "PATH": os.defpath,
}
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_VERSION_TEXT = re.compile(r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")
_ISOINFO_DIRECTORY = re.compile(r"^Directory listing of (?P<path>/.*)$")
_ISOINFO_ENTRY = re.compile(
    r"^(?P<mode>[bcdlps-][rwxStTs-]{9})\s+.*\]\s+"
    r"(?P<name>[^\r\n]+)$"
)
_XORRISO_ENTRY = re.compile(
    r"^(?P<mode>[bcdlps-][rwxStTs-]{9})(?:[+@.]?)\s+.*\s+"
    r"'(?P<path>/[^']*)'$"
)


@dataclass(frozen=True)
class VersionQuerySpec:
    """One accepted executable and its exact version-query contract."""

    tool: str
    arguments: Tuple[str, ...]
    version_pattern: str


VERSION_QUERY_SPECS = (
    VersionQuerySpec(
        "mksquashfs",
        ("-version",),
        r"(?im)^mksquashfs version [^\r\n]+$",
    ),
    VersionQuerySpec(
        "unsquashfs",
        ("-version",),
        r"(?im)^unsquashfs version [^\r\n]+$",
    ),
    VersionQuerySpec(
        "rsync",
        ("--version",),
        r"(?im)^rsync\s+version\s+[^\r\n]+$",
    ),
    VersionQuerySpec(
        "genisoimage",
        ("-version",),
        r"(?im)^genisoimage(?:\s+version)?\s+[^\r\n]*\d[^\r\n]*$",
    ),
    VersionQuerySpec(
        "isohybrid",
        ("-V",),
        r"(?im)^isohybrid version [^\r\n]+$",
    ),
    VersionQuerySpec(
        "chroot",
        ("--version",),
        r"(?im)^chroot\s+.+\s+\d[^\r\n]*$",
    ),
    VersionQuerySpec(
        "mount",
        ("--version",),
        r"(?im)^mount from util-linux [^\r\n]+$",
    ),
    VersionQuerySpec(
        "umount",
        ("--version",),
        r"(?im)^umount from util-linux [^\r\n]+$",
    ),
    VersionQuerySpec(
        "xorriso",
        ("-version",),
        r"(?im)^xorriso\s+[^\r\n]*\d[^\r\n]*$",
    ),
    VersionQuerySpec(
        "qemu-system-x86_64",
        ("-version",),
        r"(?im)^QEMU emulator version [^\r\n]+$",
    ),
)
VERSION_TOOL_ORDER = tuple(spec.tool for spec in VERSION_QUERY_SPECS)
_VERSION_SPEC_BY_TOOL = {
    spec.tool: spec
    for spec in VERSION_QUERY_SPECS
}
_INSPECTOR_ORDER = ("isoinfo", "xorriso")
_REQUIRED_MEDIA_NODES = {
    "/.disk": "directory",
    "/casper": "directory",
    "/isolinux": "directory",
    "/isolinux/isolinux.bin": "file",
}


@dataclass(frozen=True)
class CommandOutcome:
    """Bounded process outcome before semantic classification."""

    returncode: Optional[int]
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    output_limited: bool = False
    error_type: Optional[str] = None


@dataclass(frozen=True)
class RuntimeProbeResult:
    """One independently classified runtime observation."""

    probe_id: str
    status: str
    command: Tuple[str, ...]
    evidence: Mapping[str, Any]

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError("Runtime probe status is invalid")
        if not isinstance(self.probe_id, str) or not self.probe_id:
            raise ValueError("Runtime probe identifier is required")
        if not isinstance(self.command, tuple):
            raise TypeError("Runtime probe command must be a tuple")
        if not isinstance(self.evidence, Mapping):
            raise TypeError("Runtime probe evidence must be a mapping")

    def to_dict(self):
        return {
            "command": list(self.command),
            "evidence": preflight._sanitize(self.evidence),
            "factory_authority_granted": False,
            "probe_id": self.probe_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class RuntimeEvidence:
    """Deterministic Phase 1E-B1 evidence without an authorization verdict."""

    version_queries: Tuple[RuntimeProbeResult, ...]
    source_media: Optional[RuntimeProbeResult] = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        queries = tuple(self.version_queries)
        if any(
            not isinstance(result, RuntimeProbeResult)
            for result in queries
        ):
            raise TypeError("Runtime version evidence is invalid")
        if (
            self.source_media is not None
            and not isinstance(self.source_media, RuntimeProbeResult)
        ):
            raise TypeError("Runtime source-media evidence is invalid")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("Runtime evidence schema version is invalid")
        object.__setattr__(self, "version_queries", queries)

    @property
    def counts(self):
        results = list(self.version_queries)
        if self.source_media is not None:
            results.append(self.source_media)
        return {
            status: sum(result.status == status for result in results)
            for status in RESULT_STATUSES
        }

    def to_dict(self):
        return {
            "counts": self.counts,
            "factory_authority_granted": False,
            "phase": "1E-B1",
            "schema_version": self.schema_version,
            "source_media": (
                None
                if self.source_media is None
                else self.source_media.to_dict()
            ),
            "version_queries": [
                result.to_dict()
                for result in self.version_queries
            ],
        }

    def to_json(self, indent=None):
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
        )


def _node_identity(state):
    return {
        "device": state.st_dev,
        "inode": state.st_ino,
        "mode": stat.S_IMODE(state.st_mode),
        "modified_ns": state.st_mtime_ns,
        "size_bytes": state.st_size,
    }


def _same_node(left, right):
    return _node_identity(left) == _node_identity(right)


def _decode_output(payload):
    return payload.decode("utf-8", errors="replace")


def _safe_output(payload):
    text = _decode_output(payload)
    if "\ufffd" in text or not _SAFE_VERSION_TEXT.fullmatch(text):
        raise ValueError("Command output is not valid bounded text")
    return text


def _signal_process(process, signal_number):
    try:
        os.killpg(process.pid, signal_number)
    except (AttributeError, OSError):
        try:
            if signal_number == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except OSError:
            pass


def _stop_process(process):
    if process.poll() is not None:
        return
    _signal_process(process, signal.SIGTERM)
    try:
        process.wait(timeout=_STOP_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_process(process, signal.SIGKILL)
    try:
        process.wait(timeout=_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _bounded_execute(
    command,
    *,
    timeout_seconds,
    output_limit_bytes,
    pass_fds=(),
    environment=None,
    popen=None,
    monotonic=None,
):
    """Execute one absolute argv with bounded time and retained output."""

    command = tuple(command)
    if (
        not command
        or not all(isinstance(value, str) and "\x00" not in value for value in command)
        or not os.path.isabs(command[0])
        or not _valid_timeout(timeout_seconds)
        or type(output_limit_bytes) is not int
        or output_limit_bytes <= 0
    ):
        return CommandOutcome(
            None,
            error_type="InvalidCommandContract",
        )
    process_factory = subprocess.Popen if popen is None else popen
    clock = time.monotonic if monotonic is None else monotonic
    probe_environment = dict(
        _PROBE_ENVIRONMENT
        if environment is None
        else environment
    )
    try:
        process = process_factory(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=tuple(pass_fds),
            env=probe_environment,
            start_new_session=True,
        )
    except Exception as error:
        return CommandOutcome(
            None,
            error_type=type(error).__name__,
        )

    buffers = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    total = [0]
    output_limited = threading.Event()
    reader_error = []
    buffer_lock = threading.Lock()

    def drain(stream, name):
        try:
            while True:
                chunk = stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                with buffer_lock:
                    remaining = output_limit_bytes - total[0]
                    if remaining > 0:
                        retained = chunk[:remaining]
                        buffers[name].extend(retained)
                        total[0] += len(retained)
                    if len(chunk) > remaining:
                        output_limited.set()
        except Exception as error:
            reader_error.append(type(error).__name__)

    readers = (
        threading.Thread(
            target=drain,
            args=(process.stdout, "stdout"),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, "stderr"),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    deadline = clock() + float(timeout_seconds)
    timed_out = False
    while process.poll() is None:
        if output_limited.is_set():
            _stop_process(process)
            break
        if clock() >= deadline:
            timed_out = True
            _stop_process(process)
            break
        output_limited.wait(0.01)

    if process.poll() is None:
        _stop_process(process)
    for reader in readers:
        reader.join(_STOP_GRACE_SECONDS * 4)
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except Exception:
            pass
    if any(reader.is_alive() for reader in readers):
        reader_error.append("ReaderDidNotStop")

    return CommandOutcome(
        process.poll(),
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        timed_out=timed_out,
        output_limited=output_limited.is_set(),
        error_type=reader_error[0] if reader_error else None,
    )


def _valid_timeout(value):
    if type(value) is int:
        return value > 0
    if type(value) is float:
        return math.isfinite(value) and value > 0
    return False


def _default_resolver(tool):
    return shutil.which(tool, path=_PROBE_ENVIRONMENT["PATH"])


def _resolve_executable(tool, resolver):
    try:
        resolved = resolver(tool)
    except Exception as error:
        return None, None, type(error).__name__
    if resolved is None:
        return None, None, None
    try:
        requested = os.path.abspath(os.fspath(resolved))
        canonical = os.path.realpath(requested)
        state = os.stat(canonical)
        if not stat.S_ISREG(state.st_mode):
            raise ValueError("Executable is not a regular file")
        if not os.access(canonical, os.X_OK):
            raise PermissionError("Executable is not executable")
    except Exception as error:
        return None, None, type(error).__name__
    return canonical, state, None


def _command_evidence(outcome):
    evidence = {
        "error_type": outcome.error_type,
        "output_limit_exceeded": outcome.output_limited,
        "returncode": outcome.returncode,
        "stderr": _decode_output(outcome.stderr),
        "stdout": _decode_output(outcome.stdout),
        "timed_out": outcome.timed_out,
    }
    return evidence


def _classify_outcome(outcome):
    if outcome.error_type is not None:
        return STATUS_EXECUTION_ERROR
    if outcome.timed_out:
        return STATUS_TIMEOUT
    if outcome.output_limited:
        return STATUS_MALFORMED
    if outcome.returncode != 0:
        return STATUS_NONZERO
    return STATUS_SUCCESS


def _version_line(spec, stdout, stderr):
    combined = "\n".join(
        value
        for value in (_safe_output(stdout), _safe_output(stderr))
        if value
    )
    match = re.search(spec.version_pattern, combined)
    if match is None:
        raise ValueError("Version output does not match the accepted form")
    return match.group(0)


def _normalize_iso_path(value, allow_trailing=False):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("ISO path is invalid")
    normalized = posixpath.normpath(value)
    accepted = {normalized}
    if allow_trailing and normalized != "/":
        accepted.add(normalized + "/")
    if value not in accepted or "/../" in value or "/./" in value:
        raise ValueError("ISO path is not normalized")
    return normalized


def _record_media_node(records, path, kind):
    path = _normalize_iso_path(path)
    if kind not in {"directory", "file", "other"}:
        raise ValueError("ISO node kind is invalid")
    previous = records.get(path)
    if previous is not None and previous != kind:
        raise ValueError("ISO listing contains conflicting node types")
    records[path] = kind


def _parse_isoinfo_listing(text):
    records = {}
    current_directory = None
    observed = 0
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        directory_match = _ISOINFO_DIRECTORY.fullmatch(line)
        if directory_match is not None:
            current_directory = _normalize_iso_path(
                directory_match.group("path"),
                allow_trailing=True,
            )
            _record_media_node(
                records,
                current_directory,
                "directory",
            )
            observed += 1
            continue
        entry_match = _ISOINFO_ENTRY.fullmatch(line)
        if entry_match is None or current_directory is None:
            raise ValueError("isoinfo listing contains an unknown record")
        name = entry_match.group("name")
        if name.endswith(" "):
            name = name[:-1]
        if not name or name.endswith(" "):
            raise ValueError(
                "isoinfo listing contains an ambiguous trailing-space name"
            )
        if name in {".", ".."}:
            continue
        if "/" in name or "\x00" in name:
            raise ValueError("isoinfo listing contains an invalid name")
        path = posixpath.join(current_directory, name)
        node_type = entry_match.group("mode")[0]
        kind = (
            "directory"
            if node_type == "d"
            else "file"
            if node_type == "-"
            else "other"
        )
        _record_media_node(records, path, kind)
        observed += 1
    if observed == 0:
        raise ValueError("isoinfo listing is empty")
    return records


def _parse_xorriso_listing(text):
    records = {}
    observed = 0
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        match = _XORRISO_ENTRY.fullmatch(line)
        if match is None:
            raise ValueError("xorriso listing contains an unknown record")
        node_type = match.group("mode")[0]
        kind = (
            "directory"
            if node_type == "d"
            else "file"
            if node_type == "-"
            else "other"
        )
        _record_media_node(records, match.group("path"), kind)
        observed += 1
    if observed == 0:
        raise ValueError("xorriso listing is empty")
    return records


def _profile_evidence(records):
    observed = {
        path: records.get(path)
        for path in sorted(_REQUIRED_MEDIA_NODES)
    }
    issues = [
        "{}:expected-{}:observed-{}".format(
            path,
            _REQUIRED_MEDIA_NODES[path],
            observed[path],
        )
        for path in sorted(_REQUIRED_MEDIA_NODES)
        if observed[path] != _REQUIRED_MEDIA_NODES[path]
    ]
    return {
        "issues": issues,
        "profile": (
            "legacy-isolinux-single-filesystem-source-media"
            if not issues
            else None
        ),
        "recognized": not issues,
        "required_nodes": observed,
    }


def _source_finding_fields(source_finding):
    if isinstance(source_finding, preflight.Finding):
        check_id = source_finding.check_id
        status = source_finding.status
        evidence = source_finding.evidence
    elif isinstance(source_finding, Mapping):
        check_id = source_finding.get("check_id")
        status = source_finding.get("status")
        evidence = source_finding.get("evidence")
    else:
        raise TypeError("Phase 1E-A source evidence is invalid")
    if (
        check_id != "input.source-iso"
        or status != preflight.STATUS_PASS
        or not isinstance(evidence, Mapping)
        or evidence.get("content_observed") is not True
    ):
        raise ValueError("Phase 1E-A source custody is not accepted")
    accepted = evidence.get("accepted_source_identity")
    accepted_after = evidence.get("accepted_source_identity_after")
    digest = evidence.get("sha256")
    if (
        not isinstance(accepted, Mapping)
        or dict(accepted) != dict(accepted_after or {})
        or set(accepted) != {"dev", "ino", "mode", "path", "size"}
        or not isinstance(digest, str)
        or _HEX_SHA256.fullmatch(digest) is None
    ):
        raise ValueError("Phase 1E-A source custody fields are invalid")
    identity = dict(accepted)
    if (
        not isinstance(identity["path"], str)
        or not os.path.isabs(identity["path"])
        or os.path.normpath(identity["path"]) != identity["path"]
        or any(type(identity[key]) is not int for key in ("dev", "ino", "mode", "size"))
    ):
        raise ValueError("Phase 1E-A source identity is invalid")
    return identity, digest


def _open_source(identity, expected_digest):
    path = identity["path"]
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        state = os.fstat(descriptor)
        path_state = os.lstat(path)
        accepted = mounts.iso_source_identity(path)
        if (
            not stat.S_ISREG(state.st_mode)
            or stat.S_ISLNK(path_state.st_mode)
            or state.st_nlink != 1
            or not _same_node(state, path_state)
            or accepted != identity
        ):
            raise ValueError("Source identity differs from Phase 1E-A")
        digest = _hash_descriptor(descriptor)
        if digest != expected_digest:
            raise ValueError("Source digest differs from Phase 1E-A")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, state, digest


def _hash_descriptor(descriptor):
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _validate_source_after(descriptor, initial_state, identity, digest):
    descriptor_state = os.fstat(descriptor)
    path_state = os.lstat(identity["path"])
    accepted = mounts.iso_source_identity(identity["path"])
    after_digest = _hash_descriptor(descriptor)
    if (
        not _same_node(initial_state, descriptor_state)
        or not _same_node(initial_state, path_state)
        or accepted != identity
        or after_digest != digest
    ):
        raise ValueError("Source custody changed during inspection")
    return accepted, after_digest


class RuntimeEvidenceEngine:
    """Execute only the bounded Phase 1E-B1 evidence probes."""

    def __init__(
        self,
        resolver=None,
        executor=None,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        version_output_limit=VERSION_OUTPUT_LIMIT_BYTES,
        inspection_output_limit=INSPECTION_OUTPUT_LIMIT_BYTES,
    ):
        if not _valid_timeout(timeout_seconds):
            raise ValueError(
                "Runtime timeout must be a finite positive number"
            )
        self.resolver = _default_resolver if resolver is None else resolver
        self.executor = _bounded_execute if executor is None else executor
        self.timeout_seconds = timeout_seconds
        self.version_output_limit = version_output_limit
        self.inspection_output_limit = inspection_output_limit

    def _execute(
        self,
        command,
        *,
        output_limit,
        pass_fds=(),
    ):
        return self.executor(
            tuple(command),
            timeout_seconds=self.timeout_seconds,
            output_limit_bytes=output_limit,
            pass_fds=tuple(pass_fds),
            environment=dict(_PROBE_ENVIRONMENT),
        )

    def query_version(self, tool):
        if tool not in _VERSION_SPEC_BY_TOOL:
            raise ValueError("Version query is not in the explicit whitelist")
        spec = _VERSION_SPEC_BY_TOOL[tool]
        executable, before_state, resolution_error = _resolve_executable(
            tool,
            self.resolver,
        )
        base_evidence = {
            "environment": {
                "LANGUAGE": "C",
                "LC_ALL": "C",
            },
            "output_limit_bytes": self.version_output_limit,
            "query_arguments": list(spec.arguments),
            "shell": False,
            "stdin": "disabled",
            "timeout_seconds": self.timeout_seconds,
            "tool": tool,
        }
        if executable is None:
            if resolution_error is not None:
                base_evidence["error_type"] = resolution_error
                return RuntimeProbeResult(
                    "version." + tool,
                    STATUS_EXECUTION_ERROR,
                    tuple(),
                    base_evidence,
                )
            base_evidence["discovered"] = False
            return RuntimeProbeResult(
                "version." + tool,
                STATUS_ABSENT,
                tuple(),
                base_evidence,
            )

        command = (executable,) + spec.arguments
        base_evidence.update(
            {
                "discovered": True,
                "executable_identity_before": _node_identity(before_state),
                "executable_path": executable,
            }
        )
        outcome = self._execute(
            command,
            output_limit=self.version_output_limit,
        )
        status = _classify_outcome(outcome)
        evidence = dict(base_evidence)
        evidence.update(_command_evidence(outcome))
        try:
            after_state = os.stat(executable)
            evidence["executable_identity_after"] = _node_identity(
                after_state
            )
            if not _same_node(before_state, after_state):
                raise ValueError("Executable identity changed")
        except Exception as error:
            evidence["error_type"] = type(error).__name__
            status = (
                STATUS_MALFORMED
                if outcome.returncode == 0
                and not outcome.timed_out
                and not outcome.output_limited
                and outcome.error_type is None
                else STATUS_EXECUTION_ERROR
            )
        else:
            if status in {STATUS_SUCCESS, STATUS_NONZERO}:
                try:
                    version_line = _version_line(
                        spec,
                        outcome.stdout,
                        outcome.stderr,
                    )
                except Exception as error:
                    evidence["version_output_matched"] = False
                    if status == STATUS_SUCCESS:
                        evidence["error_type"] = type(error).__name__
                        status = STATUS_MALFORMED
                else:
                    evidence["version_output_matched"] = True
                    evidence["version_line"] = version_line
        return RuntimeProbeResult(
            "version." + tool,
            status,
            command,
            evidence,
        )

    def query_all_versions(self):
        return tuple(
            self.query_version(tool)
            for tool in VERSION_TOOL_ORDER
        )

    def _select_inspector(self, requested=None):
        if requested is not None and requested not in _INSPECTOR_ORDER:
            raise ValueError("Source inspector is not in the whitelist")
        providers = (
            (requested,)
            if requested is not None
            else _INSPECTOR_ORDER
        )
        for provider in providers:
            executable, state, error_type = _resolve_executable(
                provider,
                self.resolver,
            )
            if executable is not None:
                return provider, executable, state, None
            if error_type is not None:
                return provider, None, None, error_type
        return None, None, None, None

    @staticmethod
    def _inspector_command(provider, executable, descriptor):
        source = "/proc/self/fd/{}".format(descriptor)
        if provider == "isoinfo":
            return (
                executable,
                "-R",
                "-l",
                "-i",
                source,
            )
        if provider == "xorriso":
            return (
                executable,
                "-no_rc",
                "-report_about",
                "FAILURE",
                "-indev",
                source,
                "-iso_rr_pattern",
                "off",
                "-lsdl",
                "/isolinux",
                "/isolinux/isolinux.bin",
                "/casper",
                "/.disk",
            )
        raise ValueError("Source inspector is not in the whitelist")

    def inspect_source_media(self, source_finding, provider=None):
        base_evidence = {
            "factory_authority_granted": False,
            "inspection_output_limit_bytes": self.inspection_output_limit,
            "preferred_provider": "isoinfo",
            "requested_provider": provider,
            "timeout_seconds": self.timeout_seconds,
        }
        try:
            identity, accepted_digest = _source_finding_fields(
                source_finding
            )
            descriptor, source_state, before_digest = _open_source(
                identity,
                accepted_digest,
            )
        except Exception as error:
            base_evidence["error_type"] = type(error).__name__
            return RuntimeProbeResult(
                "media.source-profile-runtime",
                STATUS_CUSTODY_FAILURE,
                tuple(),
                base_evidence,
            )

        try:
            selected, executable, executable_state, resolution_error = (
                self._select_inspector(provider)
            )
            base_evidence.update(
                {
                    "phase_1e_a_sha256": accepted_digest,
                    "selected_provider": selected,
                    "source_identity_before": identity,
                    "source_sha256_before": before_digest,
                }
            )
            if executable is None:
                if resolution_error is not None:
                    base_evidence["error_type"] = resolution_error
                    return RuntimeProbeResult(
                        "media.source-profile-runtime",
                        STATUS_EXECUTION_ERROR,
                        tuple(),
                        base_evidence,
                    )
                return RuntimeProbeResult(
                    "media.source-profile-runtime",
                    STATUS_ABSENT,
                    tuple(),
                    base_evidence,
                )

            command = self._inspector_command(
                selected,
                executable,
                descriptor,
            )
            base_evidence.update(
                {
                    "executable_identity_before": _node_identity(
                        executable_state
                    ),
                    "executable_path": executable,
                    "source_argument": command[
                        command.index("-i") + 1
                        if selected == "isoinfo"
                        else command.index("-indev") + 1
                    ],
                }
            )
            outcome = self._execute(
                command,
                output_limit=self.inspection_output_limit,
                pass_fds=(descriptor,),
            )
            status = _classify_outcome(outcome)
            evidence = dict(base_evidence)
            evidence.update(_command_evidence(outcome))

            try:
                executable_after = os.stat(executable)
                evidence["executable_identity_after"] = _node_identity(
                    executable_after
                )
                if not _same_node(executable_state, executable_after):
                    raise ValueError("Inspector executable identity changed")
                accepted_after, digest_after = _validate_source_after(
                    descriptor,
                    source_state,
                    identity,
                    accepted_digest,
                )
                evidence.update(
                    {
                        "source_identity_after": accepted_after,
                        "source_sha256_after": digest_after,
                    }
                )
            except Exception as error:
                evidence["error_type"] = type(error).__name__
                return RuntimeProbeResult(
                    "media.source-profile-runtime",
                    STATUS_CUSTODY_FAILURE,
                    command,
                    evidence,
                )

            if status == STATUS_SUCCESS:
                try:
                    stdout = _safe_output(outcome.stdout)
                    _safe_output(outcome.stderr)
                    records = (
                        _parse_isoinfo_listing(stdout)
                        if selected == "isoinfo"
                        else _parse_xorriso_listing(stdout)
                    )
                    profile = _profile_evidence(records)
                    evidence["profile"] = profile
                    status = (
                        STATUS_SUCCESS
                        if profile["recognized"]
                        else STATUS_PROFILE_REJECTED
                    )
                except Exception as error:
                    evidence["error_type"] = type(error).__name__
                    status = STATUS_MALFORMED
            return RuntimeProbeResult(
                "media.source-profile-runtime",
                status,
                command,
                evidence,
            )
        finally:
            os.close(descriptor)

    def collect(self, source_finding=None):
        media = (
            None
            if source_finding is None
            else self.inspect_source_media(source_finding)
        )
        return RuntimeEvidence(
            self.query_all_versions(),
            source_media=media,
        )
