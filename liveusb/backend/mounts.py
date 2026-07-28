"""Exact mount evidence, confined mount plans, and X access helpers."""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from . import run_ok
from .. import messages


MOUNT_CREATED = "created"
MOUNT_ALREADY_PRESENT = "already-mounted"
MOUNT_FAILED = "failed"
MOUNT_AMBIGUOUS = "ambiguous"
UNMOUNTED = "unmounted"
UNMOUNT_NOT_PRESENT = "not-mounted"
UNMOUNT_FAILED = "failed"
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")
_X_STATUS_ENABLED = (
    "access control enabled, only authorized clients can connect"
)
_X_STATUS_DISABLED = (
    "access control disabled, clients can connect from any host"
)
_X_ENTRY = re.compile(
    r"(?:LOCAL:|(?:SI|INET|INET6|DNET|NIS|KRB):[^\s:]+(?::[^\s:]+)*)"
)
_SEMANTIC_BOOLEAN_OPTIONS = (
    "nodev",
    "noexec",
    "nosuid",
)
_SEMANTIC_ATIME_OPTIONS = (
    "noatime",
    "nodiratime",
    "relatime",
    "strictatime",
    "lazytime",
)
_PROPAGATION_PREFIXES = (
    "shared",
    "master",
    "propagate_from",
)
ISO_MOUNT_LABEL = "ISO image"
ISO_MOUNT_OPTIONS = ("-t", "iso9660", "-o", "ro,loop")


class MountEvidenceError(messages.LiveUSBError):
    """Structured failure for invalid or ambiguous mount evidence."""


class XAccessEvidenceError(messages.LiveUSBError):
    """Structured failure for unknown X access-control evidence."""


@dataclass(frozen=True)
class MountIdentity:
    mount_id: int
    parent_id: int
    major_minor: str
    root: str
    mount_point: str
    mount_options: Tuple[str, ...]
    optional_fields: Tuple[str, ...]
    fs_type: str
    source: str
    super_options: Tuple[str, ...]

    @property
    def key(self):
        return (
            self.mount_id,
            self.parent_id,
            self.major_minor,
            self.root,
            self.mount_point,
            self.mount_options,
            self.optional_fields,
            self.fs_type,
            self.source,
            self.super_options,
        )

    def to_record(self):
        return {
            "fs_type": self.fs_type,
            "major_minor": self.major_minor,
            "mount_id": self.mount_id,
            "mount_options": list(self.mount_options),
            "mount_point": self.mount_point,
            "optional_fields": list(self.optional_fields),
            "parent_id": self.parent_id,
            "root": self.root,
            "source": self.source,
            "super_options": list(self.super_options),
        }

    @classmethod
    def from_record(cls, value):
        expected = {
            "fs_type",
            "major_minor",
            "mount_id",
            "mount_options",
            "mount_point",
            "optional_fields",
            "parent_id",
            "root",
            "source",
            "super_options",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise MountEvidenceError("Mount identity metadata is invalid")
        if (
            type(value["mount_id"]) is not int
            or value["mount_id"] < 1
            or type(value["parent_id"]) is not int
            or value["parent_id"] < 0
        ):
            raise MountEvidenceError("Mount identity numbers are invalid")
        string_fields = (
            "fs_type",
            "major_minor",
            "mount_point",
            "root",
            "source",
        )
        if any(
            not isinstance(value[field], str)
            or not value[field]
            for field in string_fields
        ):
            raise MountEvidenceError("Mount identity strings are invalid")
        list_fields = (
            "mount_options",
            "optional_fields",
            "super_options",
        )
        if any(
            not isinstance(value[field], list)
            or any(
                not isinstance(item, str)
                for item in value[field]
            )
            for field in list_fields
        ):
            raise MountEvidenceError("Mount identity options are invalid")
        if (
            not os.path.isabs(value["mount_point"])
            or os.path.normpath(value["mount_point"])
            != value["mount_point"]
        ):
            raise MountEvidenceError(
                "Mount identity path is invalid"
            )
        return cls(
            mount_id=value["mount_id"],
            parent_id=value["parent_id"],
            major_minor=value["major_minor"],
            root=value["root"],
            mount_point=value["mount_point"],
            mount_options=tuple(value["mount_options"]),
            optional_fields=tuple(value["optional_fields"]),
            fs_type=value["fs_type"],
            source=value["source"],
            super_options=tuple(value["super_options"]),
        )


@dataclass(frozen=True)
class MountRequest:
    source: str
    destination: str
    label: str
    options: Tuple[str, ...]
    recursive: bool = False


@dataclass(frozen=True)
class MountAcquisition:
    request: MountRequest
    outcome: str
    before: Tuple[MountIdentity, ...] = ()
    owned: Tuple[MountIdentity, ...] = ()
    observed_after: Tuple[MountIdentity, ...] = ()
    error: Optional[BaseException] = None

    @property
    def source(self):
        return self.request.source

    @property
    def destination(self):
        return self.request.destination

    @property
    def label(self):
        return self.request.label


@dataclass(frozen=True)
class UnmountResult:
    identity: MountIdentity
    outcome: str
    error: Optional[BaseException] = None

    @property
    def destination(self):
        return self.identity.mount_point

    @property
    def label(self):
        return self.identity.mount_point


@dataclass(frozen=True)
class XAccessState:
    enabled: bool
    local_present: bool

    def to_record(self):
        return {
            "enabled": self.enabled,
            "local_present": self.local_present,
        }

    @classmethod
    def from_record(cls, value):
        if (
            not isinstance(value, dict)
            or set(value) != {"enabled", "local_present"}
            or type(value["enabled"]) is not bool
            or type(value["local_present"]) is not bool
        ):
            raise XAccessEvidenceError(
                "X access-state metadata is invalid"
            )
        return cls(
            enabled=value["enabled"],
            local_present=value["local_present"],
        )


def _decode_mount_field(value):
    return _MOUNT_ESCAPE.sub(
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def parse_mountinfo(text):
    if not isinstance(text, str):
        raise MountEvidenceError("Mountinfo payload is invalid")
    identities = []
    seen_ids = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        left, separator, right = line.partition(" - ")
        left_fields = left.split()
        right_fields = right.split()
        if (
            not separator
            or len(left_fields) < 6
            or len(right_fields) < 3
        ):
            raise MountEvidenceError(
                f"Mountinfo line {line_number} is invalid"
            )
        try:
            mount_id = int(left_fields[0])
            parent_id = int(left_fields[1])
        except ValueError as error:
            raise MountEvidenceError(
                f"Mountinfo line {line_number} has invalid identifiers"
            ) from error
        if mount_id < 1 or parent_id < 0 or mount_id in seen_ids:
            raise MountEvidenceError(
                f"Mountinfo line {line_number} is ambiguous"
            )
        seen_ids.add(mount_id)
        mount_point = _decode_mount_field(left_fields[4])
        if (
            not os.path.isabs(mount_point)
            or os.path.normpath(mount_point) != mount_point
        ):
            raise MountEvidenceError(
                f"Mountinfo line {line_number} has an invalid path"
            )
        identities.append(
            MountIdentity(
                mount_id=mount_id,
                parent_id=parent_id,
                major_minor=left_fields[2],
                root=_decode_mount_field(left_fields[3]),
                mount_point=mount_point,
                mount_options=tuple(left_fields[5].split(",")),
                optional_fields=tuple(left_fields[6:]),
                fs_type=right_fields[0],
                source=_decode_mount_field(right_fields[1]),
                super_options=tuple(right_fields[2].split(",")),
            )
        )
    return tuple(identities)


def read_mountinfo(path="/proc/self/mountinfo"):
    with open(path, encoding="utf-8", errors="strict") as handle:
        return parse_mountinfo(handle.read())


def mounts_at(identities, path):
    target = os.path.abspath(path)
    return tuple(
        identity
        for identity in identities
        if identity.mount_point == target
    )


def _path_within(root, path, include_root=True):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    try:
        common = os.path.commonpath((root, path))
    except ValueError:
        return False
    if common != root:
        return False
    return include_root or path != root


def mounts_under(identities, root, include_root=True):
    return tuple(
        identity
        for identity in identities
        if _path_within(
            root,
            identity.mount_point,
            include_root=include_root,
        )
    )


def identity_map(identities):
    return {identity.key: identity for identity in identities}


def node_identity(path):
    stat_result = os.lstat(path)
    if not stat.S_ISDIR(stat_result.st_mode):
        raise MountEvidenceError(
            f"Mount directory is not an lstat directory: {path}"
        )
    return {
        "dev": stat_result.st_dev,
        "ino": stat_result.st_ino,
        "kind": "directory",
        "mode": stat.S_IMODE(stat_result.st_mode),
    }


def directory_identity_matches(path, expected):
    if not os.path.lexists(path):
        return False
    try:
        return node_identity(path) == expected
    except (OSError, MountEvidenceError):
        return False


def validate_mount_destination(ctx, destination):
    raw_root = os.path.abspath(ctx.fs_dir)
    canonical_root = os.path.realpath(raw_root)
    absolute = os.path.abspath(destination)
    if (
        not _path_within(raw_root, absolute, include_root=False)
        or os.path.normpath(destination) != absolute
    ):
        raise MountEvidenceError(
            f"Mount destination escapes FileSystem: {destination}"
        )
    if os.path.lexists(absolute) and os.path.islink(absolute):
        raise MountEvidenceError(
            f"Mount destination is a symbolic link: {absolute}"
        )
    parent = os.path.dirname(absolute)
    parent_real = os.path.realpath(parent)
    if not _path_within(
        canonical_root,
        parent_real,
        include_root=True,
    ):
        raise MountEvidenceError(
            f"Mount destination parent escapes FileSystem: {absolute}"
        )
    cursor = parent
    while _path_within(raw_root, cursor, include_root=True):
        if os.path.lexists(cursor):
            stat_result = os.lstat(cursor)
            if stat.S_ISLNK(stat_result.st_mode):
                resolved = os.path.realpath(cursor)
                if not _path_within(
                    canonical_root,
                    resolved,
                    include_root=True,
                ):
                    raise MountEvidenceError(
                        "Mount destination parent escapes FileSystem: "
                        f"{absolute}"
                    )
            elif not stat.S_ISDIR(stat_result.st_mode):
                raise MountEvidenceError(
                    f"Mount destination parent is not a directory: {cursor}"
                )
        if cursor == raw_root:
            break
        cursor = os.path.dirname(cursor)
    if os.path.lexists(absolute):
        stat_result = os.lstat(absolute)
        if not stat.S_ISDIR(stat_result.st_mode):
            raise MountEvidenceError(
                f"Mount destination is not a directory: {absolute}"
            )
    return absolute


def missing_directory_paths(ctx, destination):
    destination = validate_mount_destination(ctx, destination)
    raw_root = os.path.abspath(ctx.fs_dir)
    relative = os.path.relpath(destination, raw_root)
    cursor = raw_root
    missing = []
    for part in relative.split(os.sep):
        cursor = os.path.join(cursor, part)
        if os.path.lexists(cursor):
            if os.path.islink(cursor):
                raise MountEvidenceError(
                    f"Mount path component is a symbolic link: {cursor}"
                )
            if not stat.S_ISDIR(os.lstat(cursor).st_mode):
                raise MountEvidenceError(
                    f"Mount path component is not a directory: {cursor}"
                )
        else:
            missing.append(cursor)
    return tuple(missing)


def validate_extract_layout(ctx):
    """Prove that mount staging and workspace trees are disjoint."""
    work_root = os.path.realpath(os.path.abspath(ctx.work_dir))
    mount_root = os.path.realpath(os.path.abspath(ctx.mount_dir))
    if (
        work_root == mount_root
        or _path_within(work_root, mount_root, include_root=False)
        or _path_within(mount_root, work_root, include_root=False)
    ):
        raise MountEvidenceError(
            "Mount staging and workspace paths overlap"
        )
    return work_root, mount_root


def iso_mount_request(ctx, destination):
    """Build one confined ISO request for the configured image."""
    validate_extract_layout(ctx)
    source = os.path.abspath(ctx.iso)
    destination = validate_iso_mount_destination(ctx, destination)
    return MountRequest(
        source=source,
        destination=destination,
        label=ISO_MOUNT_LABEL,
        options=ISO_MOUNT_OPTIONS,
        recursive=False,
    )


def validate_iso_mount_destination(ctx, destination):
    """Validate one literal child mountpoint beneath the mount root."""
    _work_root, mount_root = validate_extract_layout(ctx)
    raw_mount_root = os.path.abspath(ctx.mount_dir)
    absolute = os.path.abspath(destination)
    if (
        os.path.normpath(destination) != absolute
        or os.path.dirname(absolute) != raw_mount_root
        or not _path_within(
            raw_mount_root,
            absolute,
            include_root=False,
        )
    ):
        raise MountEvidenceError(
            f"ISO mount destination escapes mount staging: {destination}"
        )
    if (
        not os.path.lexists(raw_mount_root)
        or os.path.islink(raw_mount_root)
        or not stat.S_ISDIR(os.lstat(raw_mount_root).st_mode)
        or os.path.realpath(raw_mount_root) != mount_root
    ):
        raise MountEvidenceError(
            "ISO mount staging root is not a literal directory"
        )
    parent_real = os.path.realpath(os.path.dirname(absolute))
    if parent_real != mount_root:
        raise MountEvidenceError(
            "ISO mount destination parent changed"
        )
    if os.path.lexists(absolute):
        state = os.lstat(absolute)
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(
            state.st_mode
        ):
            raise MountEvidenceError(
                "ISO mount destination is not a literal directory"
            )
    return absolute


def is_authorized_iso_request(ctx, request):
    if (
        request.label != ISO_MOUNT_LABEL
        or tuple(request.options) != ISO_MOUNT_OPTIONS
        or request.recursive
        or request.source != os.path.abspath(ctx.iso)
    ):
        return False
    try:
        return (
            validate_iso_mount_destination(
                ctx,
                request.destination,
            )
            == request.destination
        )
    except MountEvidenceError:
        return False


def system_mount_requests(ctx):
    return (
        MountRequest(
            source="/dev",
            destination=os.path.join(ctx.fs_dir, "dev"),
            label="/dev",
            options=("--rbind",),
            recursive=True,
        ),
        MountRequest(
            source="/proc",
            destination=os.path.join(ctx.fs_dir, "proc"),
            label="/proc",
            options=("--bind",),
        ),
        MountRequest(
            source="/sys",
            destination=os.path.join(ctx.fs_dir, "sys"),
            label="/sys",
            options=("--bind",),
        ),
    )


def dbus_mount_requests(ctx):
    first, modern, legacy = dbus_mount_alternatives(ctx)
    var_run = os.path.join(ctx.fs_dir, "var", "run")
    if os.path.islink(var_run):
        expected = os.path.realpath(os.path.join(ctx.fs_dir, "run"))
        actual = os.path.realpath(var_run)
        if actual != expected or not _path_within(
            os.path.realpath(ctx.fs_dir),
            actual,
            include_root=False,
        ):
            raise MountEvidenceError(
                f"FileSystem var/run link escapes run: {var_run}"
            )
        second = modern
    else:
        second = legacy
    return first, second


def dbus_mount_alternatives(ctx):
    """Return immutable authorized D-Bus plans for journal validation."""
    first = MountRequest(
        source="/var/lib/dbus",
        destination=os.path.join(ctx.fs_dir, "var", "lib", "dbus"),
        label="/var/lib/dbus",
        options=("--bind",),
    )
    modern = MountRequest(
        source="/run/dbus",
        destination=os.path.join(ctx.fs_dir, "run", "dbus"),
        label="/run/dbus",
        options=("--bind",),
    )
    legacy = MountRequest(
        source="/var/run/dbus",
        destination=os.path.join(
            ctx.fs_dir,
            "var",
            "run",
            "dbus",
        ),
        label="/var/run/dbus",
        options=("--bind",),
    )
    return first, modern, legacy


def authorized_mount_requests(ctx):
    """Return mount plans independent of mutable FileSystem topology."""
    return system_mount_requests(ctx) + dbus_mount_alternatives(ctx)


def run_mount(request, runner=None):
    selected_runner = run_ok if runner is None else runner
    result = selected_runner(
        ["mount"]
        + list(request.options)
        + [request.source, request.destination]
    )
    if type(result) is bool:
        return result
    return getattr(result, "returncode", 1) == 0


def run_unmount(identity, runner=None):
    selected_runner = run_ok if runner is None else runner
    result = selected_runner(
        ["umount", "-fl", identity.mount_point]
    )
    if type(result) is bool:
        return result
    return getattr(result, "returncode", 1) == 0


def attributable_mounts(request, before, after):
    before_map = identity_map(before)
    after_map = identity_map(after)
    missing_before = tuple(
        identity
        for key, identity in before_map.items()
        if key not in after_map
    )
    if missing_before:
        raise MountEvidenceError(
            "Pre-acquisition mount evidence changed"
        )
    new_identities = tuple(
        identity
        for key, identity in after_map.items()
        if key not in before_map
    )
    relevant = mounts_under(
        new_identities,
        request.destination,
        include_root=True,
    )
    roots = mounts_at(relevant, request.destination)
    if len(roots) != 1:
        raise MountEvidenceError(
            "Mount acquisition has ambiguous root evidence"
        )
    root = roots[0]
    if not request.recursive and len(relevant) != 1:
        raise MountEvidenceError(
            "Non-recursive mount created ambiguous nested evidence"
        )
    if request.recursive:
        relevant_ids = {
            identity.mount_id: identity
            for identity in relevant
        }
        for identity in relevant:
            if identity == root:
                continue
            visited = set()
            current = identity
            while current.mount_id != root.mount_id:
                if current.mount_id in visited:
                    raise MountEvidenceError(
                        "Recursive mount ancestry is cyclic"
                    )
                visited.add(current.mount_id)
                parent = relevant_ids.get(current.parent_id)
                if parent is None:
                    raise MountEvidenceError(
                        "Recursive mount ancestry is ambiguous"
                    )
                current = parent
    unrelated_new = tuple(
        identity
        for identity in new_identities
        if identity not in relevant
    )
    if unrelated_new:
        raise MountEvidenceError(
            "Mount command changed evidence outside its destination"
        )
    return tuple(relevant)


def _covering_mount(identities, path):
    candidates = tuple(
        identity
        for identity in identities
        if _path_within(
            identity.mount_point,
            path,
            include_root=True,
        )
    )
    if not candidates:
        raise MountEvidenceError(
            f"No mount identity covers source path: {path}"
        )
    longest = max(
        len(identity.mount_point.rstrip(os.sep))
        for identity in candidates
    )
    nearest = tuple(
        identity
        for identity in candidates
        if len(identity.mount_point.rstrip(os.sep)) == longest
    )
    if len(nearest) != 1:
        raise MountEvidenceError(
            f"Source mount topology is stacked or ambiguous: {path}"
        )
    return nearest[0]


def _expected_bind_root(source_mount, source_path):
    relative = os.path.relpath(
        source_path,
        source_mount.mount_point,
    )
    if relative == ".":
        return source_mount.root
    return posixpath.normpath(
        posixpath.join(
            source_mount.root,
            relative.replace(os.sep, "/"),
        )
    )


def _same_mounted_object(actual, source, expected_root=None):
    root = source.root if expected_root is None else expected_root
    return (
        actual.major_minor == source.major_minor
        and actual.fs_type == source.fs_type
        and actual.source == source.source
        and actual.root == root
        and _effective_mount_semantics(actual)
        == _effective_mount_semantics(source)
    )


def _effective_mount_semantics(identity):
    mount_options = set(identity.mount_options)
    super_options = set(identity.super_options)

    def access(options):
        if "ro" in options:
            return "ro"
        if "rw" in options:
            return "rw"
        return "unspecified"

    mount_boolean = tuple(
        option in mount_options
        for option in _SEMANTIC_BOOLEAN_OPTIONS
    )
    super_boolean = tuple(
        option in super_options
        for option in _SEMANTIC_BOOLEAN_OPTIONS
    )
    mount_atime = tuple(
        option
        for option in _SEMANTIC_ATIME_OPTIONS
        if option in mount_options
    )
    super_atime = tuple(
        option
        for option in _SEMANTIC_ATIME_OPTIONS
        if option in super_options
    )
    propagation = set()
    for field in identity.optional_fields:
        prefix = field.partition(":")[0]
        if prefix in _PROPAGATION_PREFIXES:
            propagation.add(prefix)
        elif field == "unbindable":
            propagation.add(field)
    return (
        access(mount_options),
        mount_boolean,
        mount_atime,
        access(super_options),
        super_boolean,
        super_atime,
        tuple(sorted(propagation)),
    )


def prove_preexisting_mount(request, identities):
    """Return exact equivalent destination identities or fail closed."""
    source_path = os.path.abspath(request.source)
    destination = os.path.abspath(request.destination)
    source_root = _covering_mount(identities, source_path)
    destination_identities = mounts_under(
        identities,
        destination,
        include_root=True,
    )
    destination_roots = mounts_at(
        destination_identities,
        destination,
    )
    if len(destination_roots) != 1:
        raise MountEvidenceError(
            "Pre-existing destination mount is absent, stacked, or ambiguous"
        )
    destination_root = destination_roots[0]
    if not _same_mounted_object(
        destination_root,
        source_root,
        expected_root=_expected_bind_root(
            source_root,
            source_path,
        ),
    ):
        raise MountEvidenceError(
            "Pre-existing destination mount does not match its source"
        )
    if not request.recursive:
        if len(destination_identities) != 1:
            raise MountEvidenceError(
                "Pre-existing non-recursive mount has unproved topology"
            )
        return (destination_root,)

    source_nested = mounts_under(
        identities,
        source_path,
        include_root=False,
    )
    source_by_path = {}
    for identity in source_nested:
        source_by_path.setdefault(identity.mount_point, []).append(identity)
    if any(len(group) != 1 for group in source_by_path.values()):
        raise MountEvidenceError(
            "Recursive source topology is stacked or ambiguous"
        )

    expected_destinations = {destination: destination_root}
    source_to_destination_id = {
        source_root.mount_id: destination_root.mount_id,
    }
    ordered_nested = sorted(
        source_nested,
        key=lambda identity: (
            identity.mount_point.count(os.sep),
            identity.mount_id,
        ),
    )
    for source_identity in ordered_nested:
        relative = os.path.relpath(
            source_identity.mount_point,
            source_path,
        )
        expected_path = os.path.normpath(
            os.path.join(destination, relative)
        )
        matches = mounts_at(
            destination_identities,
            expected_path,
        )
        if len(matches) != 1:
            raise MountEvidenceError(
                "Recursive destination topology is incomplete or stacked"
            )
        actual = matches[0]
        if not _same_mounted_object(actual, source_identity):
            raise MountEvidenceError(
                "Recursive destination mount does not match its source"
            )
        expected_parent = source_to_destination_id.get(
            source_identity.parent_id
        )
        if (
            expected_parent is not None
            and actual.parent_id != expected_parent
        ):
            raise MountEvidenceError(
                "Recursive destination ancestry is not equivalent"
            )
        expected_destinations[expected_path] = actual
        source_to_destination_id[
            source_identity.mount_id
        ] = actual.mount_id
    if set(expected_destinations) != {
        identity.mount_point
        for identity in destination_identities
    }:
        raise MountEvidenceError(
            "Recursive destination contains unproved mount identities"
        )
    return tuple(
        expected_destinations[path]
        for path in sorted(
            expected_destinations,
            key=lambda item: (
                item.count(os.sep),
                item,
            ),
        )
    )


def parse_xhost_output(text):
    if not isinstance(text, str):
        raise XAccessEvidenceError("X access output is invalid")
    lines = tuple(text.splitlines())
    if any(not line or line != line.strip() for line in lines):
        raise XAccessEvidenceError(
            "X access-control output formatting is invalid"
        )
    if not lines or lines[0] not in {
        _X_STATUS_ENABLED,
        _X_STATUS_DISABLED,
    }:
        raise XAccessEvidenceError(
            "X access-control state is unparsable"
        )
    if any(
        line.startswith("access control ")
        or _X_ENTRY.fullmatch(line) is None
        for line in lines[1:]
    ):
        raise XAccessEvidenceError(
            "X access-control entry is unparsable"
        )
    enabled = lines[0] == _X_STATUS_ENABLED
    local_present = any(
        line == "LOCAL:"
        for line in lines[1:]
    )
    return XAccessState(
        enabled=enabled,
        local_present=local_present,
    )


def _default_x_runner(command):
    environment = os.environ.copy()
    environment["LANG"] = "C"
    environment["LC_ALL"] = "C"
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )


def query_x_access(runner=None):
    selected_runner = _default_x_runner if runner is None else runner
    result = selected_runner(["xhost"])
    if getattr(result, "returncode", 1) != 0:
        raise XAccessEvidenceError(
            "Unable to query X access-control state"
        )
    return parse_xhost_output(getattr(result, "stdout", ""))


def mutate_x_access(add, runner=None):
    selected_runner = _default_x_runner if runner is None else runner
    argument = "+LOCAL:" if add else "-LOCAL:"
    result = selected_runner(["xhost", argument])
    return getattr(result, "returncode", 1) == 0


def check_lock(ctx):
    messages.info("Checking for FileSystem lock")
    if ctx.force_chroot:
        messages.warning("FileSystem lock check skipped!")
        return
    lock = os.path.join(ctx.fs_dir, "tmp", "lock_chroot")
    if os.path.exists(lock):
        raise messages.LiveUSBError("FileSystem is locked!")


def check_fs_dir(ctx):
    messages.info("Checking FileSystem")
    if not os.path.isdir(ctx.fs_dir):
        messages.error("FileSystem path does not exist!")
    for subdirectory in ("etc", "usr", "root"):
        if not os.path.isdir(os.path.join(ctx.fs_dir, subdirectory)):
            messages.error(
                "FileSystem path is not usable or has been corrupted!"
            )


def check_for_x():
    messages.info("Checking whether x-server is running")
    if not run_ok(
        ["pgrep", "Xorg"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ):
        messages.error("X-server is not running!")


def purge_work_dirs(ctx):
    check_lock(ctx)
    active_mounts = mounts_under(
        read_mountinfo(),
        ctx.fs_dir,
        include_root=True,
    )
    if active_mounts:
        raise MountEvidenceError(
            "FileSystem purge is blocked by active mount evidence"
        )
    messages.extra_info("Purging", ctx.fs_dir)
    try:
        shutil.rmtree(ctx.fs_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise messages.LiveUSBError(
            "Unable to purge FileSystem directory"
        ) from error
    messages.extra_info("Purging", ctx.iso_dir)
    try:
        shutil.rmtree(ctx.iso_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise messages.LiveUSBError(
            "Unable to purge the ISO directory"
        ) from error
