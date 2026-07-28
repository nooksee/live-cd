"""Crash-durable ownership for caller mounts, X access, and staging."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional

from . import mounts
from .. import messages


_JOURNAL_VERSION = 3
_JOURNAL_NAME = "mount-session.json"
_PENDING_MARKER = ".pending-"
_LOCK_NAME = "operation.lock"
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MOUNT_PLAN_STAGES = {
    "planned",
    "command-started",
    "owned",
    "already-present",
    "failed",
    "ambiguous",
}
_MOUNT_IDENTITY_STAGES = {
    "owned",
    "unmounting",
    "removed",
}
_DIRECTORY_STAGES = {
    "planned",
    "staged",
    "rename-planned",
    "created",
    "removing",
    "removed",
}
_ARTIFACT_STAGES = {
    "planned",
    "writing",
    "active",
    "removing",
    "removed",
}
_X_STAGES = {
    "unexamined",
    "no-change",
    "grant-planned",
    "owned",
    "revoking",
    "restored",
    "failed",
}


@dataclass(frozen=True)
class MountCleanupFailure:
    operation: str
    path: str
    error: BaseException


class MountAcquisitionError(messages.LiveUSBError):
    """Fail-closed error for incomplete resource acquisition."""

    def __init__(self, message, results=()):
        self.results = tuple(results)
        super().__init__(message)


class MountRecoveryError(messages.LiveUSBError):
    """Fail-closed error for unsafe or ambiguous recovery evidence."""


class MountSessionCleanupError(messages.LiveUSBError):
    """One ordered Python 3.8-compatible cleanup error."""

    def __init__(self, failures: Iterable[MountCleanupFailure]):
        self.failures = tuple(failures)
        details = "; ".join(
            f"{failure.operation} [{failure.path}]: {failure.error}"
            for failure in self.failures
        )
        super().__init__(f"Mount session cleanup failed: {details}")


class MountSession:
    """Own one machine-wide caller operation and its durable resources."""

    def __init__(
        self,
        ctx,
        mountinfo_reader=None,
        mount_runner=None,
        unmount_runner=None,
        x_query=None,
        x_mutator=None,
    ):
        self.ctx = ctx
        self.work_root = os.path.realpath(
            os.path.abspath(ctx.work_dir)
        )
        self.fs_root = os.path.realpath(
            os.path.abspath(ctx.fs_dir)
        )
        self.mount_root = os.path.realpath(
            os.path.abspath(ctx.mount_dir)
        )
        self.runtime_dir = os.path.abspath(ctx.runtime_dir)
        self.lock_path = os.path.join(
            self.runtime_dir,
            _LOCK_NAME,
        )
        self.journal_path = os.path.join(
            self.runtime_dir,
            _JOURNAL_NAME,
        )
        self.pending_prefix = self.journal_path + _PENDING_MARKER
        self._mountinfo_reader = (
            mounts.read_mountinfo
            if mountinfo_reader is None
            else mountinfo_reader
        )
        self._mount_runner = mount_runner
        self._unmount_runner = unmount_runner
        self._x_query = (
            mounts.query_x_access
            if x_query is None
            else x_query
        )
        self._x_mutator = (
            mounts.mutate_x_access
            if x_mutator is None
            else x_mutator
        )
        self._lock_descriptor: Optional[int] = None
        self._lock_identity = None
        self._state = None
        self._persisted_state = None
        self._journal_active = False
        self._entered = False
        self._recovering = False
        self._cleanup_attempted = False

    def __del__(self):
        descriptor = getattr(self, "_lock_descriptor", None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._lock_descriptor = None

    @property
    def token(self):
        if self._state is None:
            return None
        return self._state["owner"]["token"]

    @property
    def owned_mounts(self):
        if self._state is None:
            return tuple()
        return tuple(
            owned["identity"]["mount_point"]
            for plan in self._state["mounts"]
            for owned in plan["owned"]
            if owned["stage"] != "removed"
        )

    def __enter__(self):
        if self._entered:
            raise MountAcquisitionError(
                "A mount session instance cannot be entered twice"
            )
        self._acquire_runtime_lock()
        try:
            self._recover_existing_transaction()
            self._initialize_journal()
        except BaseException:
            self._release_runtime_lock_quietly()
            raise
        self._entered = True
        return self

    def __exit__(self, _exc_type, primary_error, _traceback):
        failures = []
        if not self._cleanup_attempted:
            try:
                self.cleanup()
            except MountSessionCleanupError as cleanup_error:
                failures.extend(cleanup_error.failures)
            except Exception as error:
                failures.append(
                    MountCleanupFailure(
                        "cleanup_mount_session",
                        self.journal_path,
                        error,
                    )
                )
        try:
            self._release_runtime_lock()
        except Exception as error:
            failures.append(
                MountCleanupFailure(
                    "release_runtime_lock",
                    self.lock_path,
                    error,
                )
            )
        if failures:
            cleanup_error = MountSessionCleanupError(failures)
            if primary_error is not None:
                raise cleanup_error from primary_error
            raise cleanup_error
        return False

    def mount_sys(self):
        return tuple(
            self._acquire_mount(request)
            for request in mounts.system_mount_requests(self.ctx)
        )

    def mount_dbus(self):
        return tuple(
            self._acquire_mount(request)
            for request in mounts.dbus_mount_requests(self.ctx)
        )

    def mount_iso(self):
        self._require_active_session()
        mounts.validate_extract_layout(self.ctx)
        mount_root = os.path.abspath(self.ctx.mount_dir)
        if not os.path.isdir(mount_root):
            raise MountAcquisitionError(
                "ISO mount staging root does not exist"
            )
        destination = os.path.join(
            mount_root,
            f"liveusb-iso-{self.token}-{uuid.uuid4().hex}",
        )
        return self._acquire_mount(
            mounts.iso_mount_request(
                self.ctx,
                destination,
            )
        )

    def allow_local_x_access(self):
        self._require_active_session()
        x_record = self._state["x"]
        if x_record["stage"] not in {
            "unexamined",
            "no-change",
        }:
            raise MountAcquisitionError(
                "X access has already been evaluated"
            )
        try:
            before = self._query_x_state()
        except Exception as error:
            raise MountAcquisitionError(
                "Unable to establish X access pre-state"
            ) from error
        x_record["before"] = before.to_record()
        if not before.enabled or before.local_present:
            x_record["stage"] = "no-change"
            x_record["mutation"] = False
            self._persist_journal()
            return before

        x_record["stage"] = "grant-planned"
        x_record["mutation"] = False
        self._persist_journal()
        if self._query_x_state() != before:
            raise MountAcquisitionError(
                "X access changed before grant execution"
            )
        command_succeeded = False
        command_error = None
        try:
            command_succeeded = self._mutate_x(True)
        except Exception as error:
            command_error = error
        try:
            after = self._query_x_state()
        except Exception as query_error:
            if command_error is not None:
                raise MountAcquisitionError(
                    "X grant and verification failed"
                ) from command_error
            raise MountAcquisitionError(
                "X grant verification is unavailable"
            ) from query_error

        if after.enabled and after.local_present:
            x_record["stage"] = "owned"
            x_record["mutation"] = True
            self._persist_journal()
            if not command_succeeded:
                raise MountAcquisitionError(
                    "X grant command failed after changing access"
                ) from command_error
            return after

        if after == before:
            x_record["stage"] = "failed"
            x_record["mutation"] = False
            self._persist_journal()
            raise MountAcquisitionError(
                "X grant did not change the LOCAL entry"
            ) from command_error

        raise MountAcquisitionError(
            "X grant produced ambiguous access state"
        ) from command_error

    def stage_file(
        self,
        source,
        purpose,
        suffix="",
        executable=False,
    ):
        self._require_active_session()
        if (
            not isinstance(purpose, str)
            or not purpose
            or not purpose.replace("-", "").isalnum()
        ):
            raise MountAcquisitionError(
                "Staging purpose is invalid"
            )
        self._ensure_directory(
            os.path.join(self.ctx.fs_dir, "tmp")
        )
        source_descriptor = self._open_source_file(source)
        try:
            source_state = os.fstat(source_descriptor)
            digest = self._hash_descriptor(source_descriptor)
            os.lseek(source_descriptor, 0, os.SEEK_SET)
            mode = stat.S_IMODE(source_state.st_mode)
            if executable:
                mode |= (
                    stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )
            name = (
                f"liveusb-{purpose}-{self.token}-"
                f"{uuid.uuid4().hex}{suffix}"
            )
            destination = os.path.join(
                self.ctx.fs_dir,
                "tmp",
                name,
            )
            destination = mounts.validate_mount_destination(
                self.ctx,
                destination,
            )
            expected = {
                "mode": mode,
                "sha256": digest,
                "size": source_state.st_size,
            }
            record = {
                "expected": expected,
                "identity": None,
                "path": destination,
                "purpose": purpose,
                "stage": "planned",
            }
            self._state["artifacts"].append(record)
            self._persist_journal()
            destination_descriptor = self._create_staged_file(
                destination
            )
            try:
                record["identity"] = (
                    self._capture_descriptor_identity(
                        destination_descriptor
                    )
                )
                record["stage"] = "writing"
                self._persist_journal()
                os.lseek(source_descriptor, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(
                        source_descriptor,
                        1024 * 1024,
                    )
                    if not chunk:
                        break
                    self._write_all(
                        destination_descriptor,
                        chunk,
                    )
                os.fchmod(destination_descriptor, mode)
                os.fsync(destination_descriptor)
                identity = self._capture_descriptor_identity(
                    destination_descriptor
                )
                if (
                    identity["sha256"] != expected["sha256"]
                    or identity["size"] != expected["size"]
                    or identity["mode"] != expected["mode"]
                ):
                    raise MountAcquisitionError(
                        "Staged artifact does not match its source"
                    )
                record["identity"] = identity
                record["stage"] = "active"
                self._persist_journal()
            finally:
                os.close(destination_descriptor)
        finally:
            os.close(source_descriptor)
        return "/" + os.path.relpath(destination, self.ctx.fs_dir)

    def cleanup(self):
        self._cleanup_attempted = True
        acquired_here = False
        recovering_here = False
        if (
            self._lock_descriptor is not None
            and not self._journal_active
        ):
            return
        if self._lock_descriptor is None:
            self._acquire_runtime_lock()
            acquired_here = True
            try:
                self._reconcile_pending_journal()
            except BaseException:
                self._release_runtime_lock_quietly()
                raise
            if not os.path.lexists(self.journal_path):
                self._release_runtime_lock()
                return
            try:
                self._load_existing_journal()
                self._recovering = True
                recovering_here = True
            except BaseException:
                self._release_runtime_lock_quietly()
                raise
        failures: List[MountCleanupFailure] = []
        try:
            self._preflight_cleanup()
            if self._state["phase"] != "cleaning":
                self._state["phase"] = "cleaning"
                self._persist_journal()

            for plan, owned in self._owned_mount_cleanup_order():
                if owned["stage"] == "removed":
                    continue
                self._attempt_cleanup(
                    failures,
                    "unmount_owned",
                    owned["identity"]["mount_point"],
                    lambda plan=plan, owned=owned: (
                        self._cleanup_owned_mount(plan, owned)
                    ),
                )

            for artifact in reversed(self._state["artifacts"]):
                if artifact["stage"] == "removed":
                    continue
                self._attempt_cleanup(
                    failures,
                    "remove_staged_artifact",
                    artifact["path"],
                    lambda artifact=artifact: (
                        self._cleanup_artifact(artifact)
                    ),
                )

            for directory in reversed(self._state["directories"]):
                if directory["stage"] == "removed":
                    continue
                if self._directory_has_active_resources(
                    directory["path"]
                ):
                    continue
                self._attempt_cleanup(
                    failures,
                    "remove_created_directory",
                    directory["path"],
                    lambda directory=directory: (
                        self._cleanup_directory(directory)
                    ),
                )

            if self._state["x"]["stage"] in {
                "owned",
                "revoking",
            }:
                self._attempt_cleanup(
                    failures,
                    "restore_x_access",
                    "LOCAL:",
                    self._cleanup_x_access,
                )

            if not failures:
                active = self._active_resource_descriptions()
                if active:
                    failures.append(
                        MountCleanupFailure(
                            "finalize_mount_session",
                            self.journal_path,
                            MountRecoveryError(
                                "Active resources remain: "
                                + ", ".join(active)
                            ),
                        )
                    )
                else:
                    self._state["phase"] = "complete"
                    self._persist_journal()
                    self._remove_journal()
        finally:
            if recovering_here:
                self._recovering = False
            if acquired_here:
                self._release_runtime_lock_quietly()
        if failures:
            raise MountSessionCleanupError(failures)

    def _acquire_mount(self, request):
        self._require_active_session()
        if request.kind == "iso":
            try:
                mounts.validate_iso_acquisition(self.ctx, request)
            except mounts.MountEvidenceError as error:
                raise MountAcquisitionError(
                    "ISO acquisition custody is invalid"
                ) from error
        try:
            destination = self._validate_mount_destination(request)
        except Exception as error:
            raise MountAcquisitionError(
                f"Invalid mount destination: {request.destination}"
            ) from error
        request = mounts.MountRequest(
            source=request.source,
            destination=destination,
            label=request.label,
            options=request.options,
            recursive=request.recursive,
            kind=request.kind,
            custody=request.custody,
        )
        before_all = self._read_mounts()
        before = mounts.mounts_under(
            before_all,
            destination,
            include_root=True,
        )
        existing = mounts.mounts_at(before_all, destination)
        if existing:
            try:
                mounts.prove_preexisting_mount(
                    request,
                    before_all,
                )
            except Exception as error:
                raise MountAcquisitionError(
                    "Pre-existing mount equivalence is unproved: "
                    f"{destination}"
                ) from error
        elif before:
            raise MountAcquisitionError(
                "Pre-existing nested mount topology is unproved: "
                f"{destination}"
            )
        plan = {
            "before": [
                identity.to_record()
                for identity in before
            ],
            "destination": destination,
            "id": uuid.uuid4().hex,
            "label": request.label,
            "observed_after": (
                [
                    identity.to_record()
                    for identity in before
                ]
                if existing
                else []
            ),
            "options": list(request.options),
            "owned": [],
            "recursive": request.recursive,
            "source": request.source,
            "kind": request.kind,
            "custody": request.custody,
            "stage": (
                "already-present"
                if existing
                else "planned"
            ),
        }
        self._state["mounts"].append(plan)
        self._persist_journal()
        if existing:
            return mounts.MountAcquisition(
                request=request,
                outcome=mounts.MOUNT_ALREADY_PRESENT,
                before=before,
                observed_after=before,
            )

        self._ensure_directory(request)
        self._validate_mount_destination(request)
        before_command = mounts.mounts_under(
            self._read_mounts(),
            destination,
            include_root=True,
        )
        if mounts.identity_map(before_command) != mounts.identity_map(
            before
        ):
            plan["stage"] = "ambiguous"
            plan["observed_after"] = [
                identity.to_record()
                for identity in before_command
            ]
            self._persist_journal()
            raise MountAcquisitionError(
                "Mount evidence changed before command execution"
            )

        plan["stage"] = "command-started"
        self._persist_journal()
        self._validate_mount_destination(request)
        command_succeeded = False
        command_error = None
        try:
            command_succeeded = mounts.run_mount(
                request,
                runner=self._mount_runner,
            )
        except Exception as error:
            command_error = error
        after = mounts.mounts_under(
            self._read_mounts(),
            destination,
            include_root=True,
        )
        plan["observed_after"] = [
            identity.to_record()
            for identity in after
        ]

        if not command_succeeded:
            plan["stage"] = "failed"
            self._persist_journal()
            acquisition = mounts.MountAcquisition(
                request=request,
                outcome=mounts.MOUNT_FAILED,
                before=before,
                observed_after=after,
                error=command_error
                or mounts.MountEvidenceError(
                    "Mount command returned failure"
                ),
            )
            raise MountAcquisitionError(
                f"Mount acquisition failed: {destination}",
                (acquisition,),
            ) from command_error

        try:
            owned = mounts.attributable_mounts(
                request,
                before,
                after,
            )
        except Exception as error:
            plan["stage"] = "ambiguous"
            self._persist_journal()
            raise MountAcquisitionError(
                f"Mount acquisition is ambiguous: {destination}"
            ) from error

        plan["owned"] = [
            {
                "identity": identity.to_record(),
                "inferred": False,
                "stage": "owned",
            }
            for identity in owned
        ]
        plan["stage"] = "owned"
        self._persist_journal()
        return mounts.MountAcquisition(
            request=request,
            outcome=mounts.MOUNT_CREATED,
            before=before,
            owned=owned,
            observed_after=after,
        )

    def _preflight_cleanup(self):
        candidate = copy.deepcopy(self._state)
        self._validate_state(candidate)
        current = self._read_mounts()
        changed = False

        for plan in candidate["mounts"]:
            before = tuple(
                mounts.MountIdentity.from_record(record)
                for record in plan["before"]
            )
            current_scoped = mounts.mounts_under(
                current,
                plan["destination"],
                include_root=True,
            )
            if plan["stage"] in {
                "planned",
                "failed",
                "ambiguous",
                "already-present",
            }:
                if mounts.identity_map(
                    current_scoped
                ) != mounts.identity_map(before):
                    raise MountRecoveryError(
                        "Interrupted or unowned mount evidence "
                        "does not match its pre-state: "
                        f"{plan['destination']}"
                    )
                if plan["stage"] == "already-present":
                    try:
                        mounts.prove_preexisting_mount(
                            self._request_from_plan(plan),
                            current,
                        )
                    except mounts.MountEvidenceError as error:
                        raise MountRecoveryError(
                            "Pre-existing mount equivalence "
                            "cannot be re-established: "
                            f"{plan['destination']}"
                        ) from error
                elif plan["stage"] != "failed":
                    plan["stage"] = "failed"
                    changed = True

            if plan["stage"] == "command-started":
                if mounts.identity_map(
                    current_scoped
                ) == mounts.identity_map(before):
                    plan["stage"] = "failed"
                    changed = True
                else:
                    try:
                        owned = self._prove_interrupted_mount(
                            plan,
                            current,
                            before,
                        )
                    except mounts.MountEvidenceError as error:
                        raise MountRecoveryError(
                            "Interrupted mount ownership is ambiguous: "
                            f"{plan['destination']}"
                        ) from error
                    plan["observed_after"] = [
                        identity.to_record()
                        for identity in current_scoped
                    ]
                    plan["owned"] = [
                        {
                            "identity": identity.to_record(),
                            "inferred": True,
                            "stage": "owned",
                        }
                        for identity in owned
                    ]
                    plan["stage"] = "owned"
                    changed = True

            if plan["stage"] == "owned":
                changed = (
                    self._preflight_owned_plan(
                        plan,
                        current,
                    )
                    or changed
                )

        for artifact in candidate["artifacts"]:
            changed = (
                self._preflight_artifact(artifact)
                or changed
            )
        active_mount_points = {
            owned["identity"]["mount_point"]
            for plan in candidate["mounts"]
            for owned in plan["owned"]
            if owned["stage"] in {"owned", "unmounting"}
        }
        for directory in candidate["directories"]:
            changed = (
                self._preflight_directory(
                    directory,
                    candidate["directories"],
                    active_mount_points,
                )
                or changed
            )
        changed = self._preflight_x(candidate["x"]) or changed

        if changed:
            self._state = candidate
            self._persist_journal()
        else:
            self._state = candidate

    def _prove_interrupted_mount(self, plan, current, before):
        """Adopt only one exact mount delta after a recorded syscall."""
        request = self._request_from_plan(plan)
        current_scoped = mounts.mounts_under(
            current,
            request.destination,
            include_root=True,
        )
        before_map = mounts.identity_map(before)
        current_map = mounts.identity_map(current_scoped)
        if any(key not in current_map for key in before_map):
            raise mounts.MountEvidenceError(
                "Interrupted mount changed its recorded pre-state"
            )
        added = tuple(
            identity
            for key, identity in current_map.items()
            if key not in before_map
        )
        if request.kind == "iso":
            mounts.validate_iso_custody(request)
            roots = mounts.mounts_at(added, request.destination)
            if (
                len(added) != 1
                or len(roots) != 1
                or roots[0].fs_type != "iso9660"
                or "ro" not in set(roots[0].mount_options).union(
                    roots[0].super_options
                )
            ):
                raise mounts.MountEvidenceError(
                    "Interrupted ISO mount delta is not exact"
                )
            return roots
        proven = mounts.prove_preexisting_mount(request, current)
        if mounts.identity_map(proven) != mounts.identity_map(added):
            raise mounts.MountEvidenceError(
                "Interrupted mount delta is not request-compatible"
            )
        return proven

    def _preflight_owned_plan(self, plan, current):
        changed = False
        before = tuple(
            mounts.MountIdentity.from_record(record)
            for record in plan["before"]
        )
        before_keys = set(mounts.identity_map(before))
        current_map = mounts.identity_map(current)
        owned_by_id = {}
        known_keys = set(before_keys)
        active_root_ids = set()

        for record in plan["owned"]:
            identity = mounts.MountIdentity.from_record(
                record["identity"]
            )
            known_keys.add(identity.key)
            owned_by_id[identity.mount_id] = record
            if record["stage"] in {"owned", "unmounting"}:
                active_root_ids.add(identity.mount_id)
            exact = current_map.get(identity.key)
            at_path = mounts.mounts_at(
                current,
                identity.mount_point,
            )
            if record["stage"] == "owned":
                if exact is None or len(at_path) != 1:
                    raise MountRecoveryError(
                        "Owned mount identity changed: "
                        f"{identity.mount_point}"
                    )
            elif record["stage"] == "unmounting":
                if exact is None:
                    if at_path:
                        raise MountRecoveryError(
                            "Unmounted identity was replaced: "
                            f"{identity.mount_point}"
                        )
                    record["stage"] = "removed"
                    active_root_ids.discard(identity.mount_id)
                    changed = True
                elif len(at_path) != 1:
                    raise MountRecoveryError(
                        "Unmounting identity is stacked or ambiguous: "
                        f"{identity.mount_point}"
                    )
            elif record["stage"] == "removed":
                if exact is not None or at_path:
                    raise MountRecoveryError(
                        "Removed mount identity was replaced: "
                        f"{identity.mount_point}"
                    )

        active_before = {
            identity.key
            for identity in before
            if identity.key in current_map
        }
        if active_before != before_keys:
            raise MountRecoveryError(
                "Pre-existing mount evidence changed beneath owned root"
            )

        scoped = mounts.mounts_under(
            current,
            plan["destination"],
            include_root=True,
        )
        extras = tuple(
            identity
            for identity in scoped
            if identity.key not in known_keys
        )
        if extras:
            if self._recovering:
                raise MountRecoveryError(
                    "Post-interruption nested mount ownership "
                    "cannot be inferred"
                )
            current_by_id = {
                identity.mount_id: identity
                for identity in current
            }
            inferred = []
            for identity in extras:
                if identity.mount_point == plan["destination"]:
                    raise MountRecoveryError(
                        "Owned mount root has a stacked replacement"
                    )
                cursor = identity
                visited = set()
                attributable = False
                while cursor.mount_id not in visited:
                    visited.add(cursor.mount_id)
                    if cursor.parent_id in active_root_ids:
                        attributable = True
                        break
                    parent = current_by_id.get(cursor.parent_id)
                    if parent is None:
                        break
                    cursor = parent
                if not attributable:
                    raise MountRecoveryError(
                        "New nested mount is not positively attributable: "
                        f"{identity.mount_point}"
                    )
                inferred.append(identity)
                active_root_ids.add(identity.mount_id)
            for identity in inferred:
                plan["owned"].append(
                    {
                        "identity": identity.to_record(),
                        "inferred": True,
                        "stage": "owned",
                    }
                )
            changed = True
        return changed

    def _preflight_directory(
        self,
        record,
        directory_records,
        active_mount_points,
    ):
        self._validate_directory_resource_path(record["path"])
        parent = os.path.dirname(record["path"])
        if (
            parent not in active_mount_points
            and not mounts.directory_identity_matches(
                parent,
                record["parent_identity"],
            )
        ):
            parent_record = next(
                (
                    candidate
                    for candidate in directory_records
                    if candidate["path"] == parent
                ),
                None,
            )
            parent_was_removed = (
                parent_record is not None
                and parent_record["stage"] in {"removing", "removed"}
                and not os.path.lexists(parent)
            )
            if not parent_was_removed:
                raise MountRecoveryError(
                    "Created-directory parent identity changed: "
                    f"{parent}"
                )
        stage = record["stage"]
        exists = os.path.lexists(record["path"])
        staging_exists = os.path.lexists(record["staging_path"])
        if stage == "planned":
            if exists:
                raise MountRecoveryError(
                    "Planned directory final path is foreign: "
                    f"{record['path']}"
                )
            if staging_exists:
                self._validate_unrecorded_staging_directory(record)
                os.chmod(
                    record["staging_path"],
                    record["desired_mode"],
                )
                self._fsync_directory(
                    os.path.dirname(record["staging_path"])
                )
                record["staging_identity"] = mounts.node_identity(
                    record["staging_path"]
                )
                record["stage"] = "staged"
                return True
            record["stage"] = "removed"
            return True
        if stage in {"staged", "rename-planned"}:
            if staging_exists and exists:
                raise MountRecoveryError(
                    "Directory transaction has two live locations"
                )
            if staging_exists:
                if not mounts.directory_identity_matches(
                    record["staging_path"],
                    record["staging_identity"],
                ):
                    raise MountRecoveryError(
                        "Staging directory identity changed"
                    )
            elif exists:
                try:
                    identity = mounts.node_identity(record["path"])
                except mounts.MountEvidenceError as error:
                    raise MountRecoveryError(
                        "Renamed directory end state is invalid: "
                        f"{record['path']}"
                    ) from error
                if identity != record["staging_identity"]:
                    raise MountRecoveryError(
                        "Renamed directory identity changed"
                    )
                record["identity"] = identity
                record["stage"] = "created"
                return True
            else:
                raise MountRecoveryError(
                    "Staged directory disappeared before cleanup"
                )
        if stage == "created":
            if staging_exists:
                raise MountRecoveryError(
                    "Created directory retained foreign staging residue"
                )
            if not exists:
                raise MountRecoveryError(
                    f"Created directory is absent: {record['path']}"
                )
            if (
                record["path"] not in active_mount_points
                and not mounts.directory_identity_matches(
                    record["path"],
                    record["identity"],
                )
            ):
                raise MountRecoveryError(
                    "Created directory identity changed: "
                    f"{record['path']}"
                )
        elif stage == "removing":
            removing_staging = record["identity"] is None
            expected_path = (
                record["staging_path"]
                if removing_staging
                else record["path"]
            )
            foreign_path = (
                record["path"]
                if removing_staging
                else record["staging_path"]
            )
            expected_identity = (
                record["staging_identity"]
                if removing_staging
                else record["identity"]
            )
            if os.path.lexists(foreign_path):
                raise MountRecoveryError(
                    "Removing directory has a live wrong location"
                )
            if not os.path.lexists(expected_path):
                record["stage"] = "removed"
                return True
            if (
                expected_path not in active_mount_points
                and not mounts.directory_identity_matches(
                    expected_path,
                    expected_identity,
                )
            ):
                raise MountRecoveryError(
                    "Removing directory identity changed: "
                    f"{expected_path}"
                )
        elif stage == "removed" and exists:
            raise MountRecoveryError(
                f"Removed directory was replaced: {record['path']}"
            )
        return False

    def _validate_unrecorded_staging_directory(self, record):
        if (
            os.path.lexists(record["path"])
            or mounts.mounts_under(
                self._read_mounts(),
                record["staging_path"],
                include_root=True,
            )
        ):
            raise MountRecoveryError(
                "Unrecorded staging directory evidence is ambiguous"
            )
        identity = mounts.node_identity(record["staging_path"])
        mode = identity["mode"]
        private_mkdir_mode = mode & ~0o700 == 0
        if (
            mode != record["desired_mode"]
            and not private_mkdir_mode
        ):
            raise MountRecoveryError(
                "Unrecorded staging directory is not exact"
            )
        normalized_for_inspection = False
        try:
            entries = os.listdir(record["staging_path"])
        except PermissionError:
            try:
                os.chmod(
                    record["staging_path"],
                    record["desired_mode"],
                )
                normalized_for_inspection = True
                self._fsync_directory(
                    os.path.dirname(record["staging_path"])
                )
                entries = os.listdir(record["staging_path"])
            except OSError as error:
                if normalized_for_inspection:
                    try:
                        os.chmod(record["staging_path"], mode)
                        self._fsync_directory(
                            os.path.dirname(record["staging_path"])
                        )
                    except OSError as restore_error:
                        raise MountRecoveryError(
                            "Unrecorded staging mode restoration failed"
                        ) from restore_error
                raise MountRecoveryError(
                    "Unrecorded staging directory cannot be inspected"
                ) from error
        except OSError as error:
            raise MountRecoveryError(
                "Unrecorded staging directory cannot be inspected"
            ) from error
        if entries:
            if normalized_for_inspection:
                try:
                    os.chmod(record["staging_path"], mode)
                    self._fsync_directory(
                        os.path.dirname(record["staging_path"])
                    )
                except OSError as error:
                    raise MountRecoveryError(
                        "Unrecorded staging mode restoration failed"
                    ) from error
            raise MountRecoveryError(
                "Unrecorded staging directory is not exact"
            )

    def _preflight_artifact(self, record):
        self._validate_resource_path(record["path"])
        stage = record["stage"]
        exists = os.path.lexists(record["path"])
        if stage == "planned":
            if exists:
                raise MountRecoveryError(
                    "Planned artifact end state is unproved: "
                    f"{record['path']}"
                )
            record["stage"] = "removed"
            return True
        if stage == "writing":
            if not exists:
                raise MountRecoveryError(
                    "Writing artifact disappeared before removal: "
                    f"{record['path']}"
                )
            actual = self._capture_file_identity(record["path"])
            if not self._same_file_node(
                actual,
                record["identity"],
            ):
                raise MountRecoveryError(
                    "Writing artifact inode changed: "
                    f"{record['path']}"
                )
        elif stage == "active":
            if not exists:
                raise MountRecoveryError(
                    "Active artifact disappeared before removal: "
                    f"{record['path']}"
                )
            if self._capture_file_identity(record["path"]) != (
                record["identity"]
            ):
                raise MountRecoveryError(
                    "Staged artifact identity changed: "
                    f"{record['path']}"
                )
        elif stage == "removing":
            if not exists:
                record["stage"] = "removed"
                return True
            if self._capture_file_identity(record["path"]) != (
                record["identity"]
            ):
                raise MountRecoveryError(
                    "Removing artifact identity changed: "
                    f"{record['path']}"
                )
        elif stage == "removed" and exists:
            raise MountRecoveryError(
                f"Removed artifact was replaced: {record['path']}"
            )
        return False

    def _preflight_x(self, x_record):
        stage = x_record["stage"]
        if stage == "grant-planned":
            before = mounts.XAccessState.from_record(
                x_record["before"]
            )
            current = self._query_x_state()
            if current == before:
                x_record["stage"] = "failed"
                x_record["mutation"] = False
                return True
            raise MountRecoveryError(
                "Interrupted X grant ownership is unproved"
            )
        if stage == "owned":
            current = self._query_x_state()
            if not current.enabled or not current.local_present:
                raise MountRecoveryError(
                    "Session-owned X entry changed"
                )
        elif stage == "revoking":
            current = self._query_x_state()
            if current.enabled and not current.local_present:
                x_record["stage"] = "restored"
                x_record["mutation"] = False
                return True
            if not current.enabled or not current.local_present:
                raise MountRecoveryError(
                    "X restoration state is ambiguous"
                )
        return False

    def _cleanup_owned_mount(self, plan, owned):
        identity = mounts.MountIdentity.from_record(
            owned["identity"]
        )
        if owned["stage"] == "unmounting":
            current = self._read_mounts()
            if identity.key not in mounts.identity_map(current):
                if mounts.mounts_at(current, identity.mount_point):
                    raise MountRecoveryError(
                        "Unmount target was replaced"
                    )
                owned["stage"] = "removed"
                self._persist_journal()
                return
        owned["stage"] = "unmounting"
        self._persist_journal()
        current = self._read_mounts()
        at_path = mounts.mounts_at(current, identity.mount_point)
        if (
            len(at_path) != 1
            or at_path[0].key != identity.key
        ):
            raise MountRecoveryError(
                f"Unmount identity changed: {identity.mount_point}"
            )
        command_succeeded = mounts.run_unmount(
            identity,
            runner=self._unmount_runner,
            lazy=plan.get("kind") != "iso",
        )
        after = self._read_mounts()
        before_keys = set(mounts.identity_map(current))
        after_keys = set(mounts.identity_map(after))
        removed_keys = before_keys - after_keys
        identity_remains = identity.key in after_keys
        replacements = mounts.mounts_at(
            after,
            identity.mount_point,
        )
        if not command_succeeded:
            raise mounts.MountEvidenceError(
                f"Unmount command failed: {identity.mount_point}"
            )
        if (
            identity_remains
            or replacements
            or removed_keys != {identity.key}
        ):
            raise mounts.MountEvidenceError(
                "Unmount completion is not exact: "
                f"{identity.mount_point}"
            )
        owned["stage"] = "removed"
        self._persist_journal()

    def _cleanup_artifact(self, artifact):
        if artifact["stage"] == "removing":
            if not os.path.lexists(artifact["path"]):
                artifact["stage"] = "removed"
                self._persist_journal()
                return
        if artifact["stage"] == "writing":
            actual = self._capture_file_identity(
                artifact["path"]
            )
            if not self._same_file_node(
                actual,
                artifact["identity"],
            ):
                raise MountRecoveryError(
                    "Writing artifact inode changed before removal"
                )
            artifact["identity"] = actual
        artifact["stage"] = "removing"
        self._persist_journal()
        if self._capture_file_identity(
            artifact["path"]
        ) != artifact["identity"]:
            raise MountRecoveryError(
                "Staged artifact identity changed before removal"
            )
        os.unlink(artifact["path"])
        self._fsync_directory(
            os.path.dirname(artifact["path"])
        )
        artifact["stage"] = "removed"
        self._persist_journal()

    @staticmethod
    def _same_file_node(first, second):
        fields = ("dev", "ino", "kind", "nlink", "owner")
        return all(
            first[field] == second[field]
            for field in fields
        )

    def _cleanup_directory(self, directory):
        if directory["stage"] in {"staged", "rename-planned"}:
            target = directory["staging_path"]
            expected = directory["staging_identity"]
        elif (
            directory["stage"] == "removing"
            and directory["identity"] is None
        ):
            target = directory["staging_path"]
            expected = directory["staging_identity"]
        else:
            target = directory["path"]
            expected = directory["identity"]
        if directory["stage"] == "removing":
            if not os.path.lexists(target):
                directory["stage"] = "removed"
                self._persist_journal()
                return
        directory["stage"] = "removing"
        self._persist_journal()
        if not mounts.directory_identity_matches(
            target,
            expected,
        ):
            raise MountRecoveryError(
                "Created directory identity changed before removal"
            )
        os.rmdir(target)
        self._fsync_directory(
            os.path.dirname(target)
        )
        directory["stage"] = "removed"
        self._persist_journal()

    def _cleanup_x_access(self):
        x_record = self._state["x"]
        before = mounts.XAccessState.from_record(
            x_record["before"]
        )
        if not before.enabled or before.local_present:
            raise MountRecoveryError(
                "Session X ownership metadata is invalid"
            )
        if x_record["stage"] != "revoking":
            x_record["stage"] = "revoking"
            self._persist_journal()
        if self._query_x_state() != mounts.XAccessState(
            enabled=True,
            local_present=True,
        ):
            raise MountRecoveryError(
                "X access changed before restoration"
            )
        command_error = None
        try:
            command_succeeded = self._mutate_x(False)
        except Exception as error:
            command_succeeded = False
            command_error = error
        try:
            current = self._query_x_state()
        except Exception as error:
            raise MountRecoveryError(
                "Unable to verify X restoration"
            ) from error
        if not command_succeeded:
            raise MountRecoveryError(
                "X restoration command failed"
            ) from command_error
        if current != before:
            raise MountRecoveryError(
                "X restoration did not reproduce the pre-state"
            )
        x_record["stage"] = "restored"
        x_record["mutation"] = False
        self._persist_journal()

    def _owned_mount_cleanup_order(self):
        records = []
        for plan_index, plan in enumerate(self._state["mounts"]):
            for owned_index, owned in enumerate(plan["owned"]):
                identity = mounts.MountIdentity.from_record(
                    owned["identity"]
                )
                records.append(
                    (
                        identity.mount_point.count(os.sep),
                        plan_index,
                        owned_index,
                        plan,
                        owned,
                    )
                )
        records.sort(
            key=lambda item: (
                item[1],
                item[0],
                item[2],
            ),
            reverse=True,
        )
        return tuple(
            (item[3], item[4])
            for item in records
        )

    def _directory_has_active_resources(self, path):
        try:
            if mounts.mounts_under(
                self._read_mounts(),
                path,
                include_root=True,
            ):
                return True
        except MountRecoveryError:
            return True
        for plan in self._state["mounts"]:
            for owned in plan["owned"]:
                if owned["stage"] == "removed":
                    continue
                identity = mounts.MountIdentity.from_record(
                    owned["identity"]
                )
                if self._path_within(
                    path,
                    identity.mount_point,
                    include_root=True,
                ):
                    return True
        for artifact in self._state["artifacts"]:
            if (
                artifact["stage"] != "removed"
                and self._path_within(
                    path,
                    artifact["path"],
                    include_root=True,
                )
            ):
                return True
        return False

    def _active_resource_descriptions(self):
        active = []
        for plan in self._state["mounts"]:
            for owned in plan["owned"]:
                if owned["stage"] != "removed":
                    active.append(
                        "mount:" + owned["identity"]["mount_point"]
                    )
            if plan["stage"] == "ambiguous":
                active.append(
                    "ambiguous-mount:" + plan["destination"]
                )
        active.extend(
            "artifact:" + artifact["path"]
            for artifact in self._state["artifacts"]
            if artifact["stage"] != "removed"
        )
        active.extend(
            "directory:" + directory["path"]
            for directory in self._state["directories"]
            if directory["stage"] != "removed"
        )
        if self._state["x"]["stage"] in {
            "grant-planned",
            "owned",
            "revoking",
        }:
            active.append("x:LOCAL:")
        return tuple(active)

    def _ensure_directory(self, request):
        if isinstance(request, mounts.MountRequest):
            destination = request.destination
            iso_request = request if request.kind == "iso" else None
        else:
            destination = request
            iso_request = None
        if iso_request is not None:
            mounts.validate_iso_custody(request)
            missing = (
                (destination,)
                if not os.path.lexists(destination)
                else ()
            )
        else:
            missing = mounts.missing_directory_paths(
                self.ctx,
                destination,
            )
        for path in missing:
            parent = os.path.dirname(path)
            token = uuid.uuid4().hex
            desired_mode = 0o700 if iso_request is not None else 0o755
            staging_path = os.path.join(
                parent,
                "." + os.path.basename(path)
                + ".liveusb-dir-" + token,
            )
            record = {
                "desired_mode": desired_mode,
                "identity": None,
                "parent_identity": mounts.node_identity(parent),
                "path": path,
                "stage": "planned",
                "staging_identity": None,
                "staging_path": staging_path,
            }
            self._state["directories"].append(record)
            self._persist_journal()
            if iso_request is not None:
                mounts.validate_iso_custody(iso_request)
            else:
                mounts.validate_mount_destination(self.ctx, path)
            if not mounts.directory_identity_matches(
                parent,
                record["parent_identity"],
            ):
                raise MountRecoveryError(
                    f"Directory parent changed before creation: {parent}"
                )
            os.mkdir(staging_path, 0o700)
            os.chmod(staging_path, desired_mode)
            self._fsync_directory(parent)
            record["staging_identity"] = mounts.node_identity(
                staging_path
            )
            record["stage"] = "staged"
            self._persist_journal()
            record["stage"] = "rename-planned"
            self._persist_journal()
            if os.path.lexists(path):
                raise MountRecoveryError(
                    f"Directory final path appeared before rename: {path}"
                )
            os.rename(staging_path, path)
            self._fsync_directory(parent)
            record["identity"] = mounts.node_identity(path)
            record["stage"] = "created"
            self._persist_journal()

    def _create_staged_file(self, destination):
        mounts.validate_mount_destination(
            self.ctx,
            destination,
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o600)
        try:
            os.fsync(descriptor)
            self._fsync_directory(
                os.path.dirname(destination)
            )
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _open_source_file(self, path):
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise MountAcquisitionError(
                f"Unable to open staging source: {path}"
            ) from error
        stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(stat_result.st_mode):
            os.close(descriptor)
            raise MountAcquisitionError(
                f"Staging source is not a regular file: {path}"
            )
        return descriptor

    def _capture_file_identity(self, path):
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            return self._capture_descriptor_identity(descriptor)
        finally:
            os.close(descriptor)

    def _capture_descriptor_identity(self, descriptor):
        stat_result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or stat_result.st_nlink != 1
            or stat_result.st_uid != os.geteuid()
        ):
            raise MountRecoveryError(
                "Staged artifact custody is invalid"
            )
        return {
            "dev": stat_result.st_dev,
            "ino": stat_result.st_ino,
            "kind": "file",
            "mode": stat.S_IMODE(stat_result.st_mode),
            "nlink": stat_result.st_nlink,
            "owner": stat_result.st_uid,
            "sha256": self._hash_descriptor(descriptor),
            "size": stat_result.st_size,
        }

    @staticmethod
    def _hash_descriptor(descriptor):
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()

    def _read_mounts(self):
        try:
            identities = tuple(self._mountinfo_reader())
        except Exception as error:
            raise MountRecoveryError(
                "Unable to read exact mount evidence"
            ) from error
        if any(
            not isinstance(identity, mounts.MountIdentity)
            for identity in identities
        ):
            raise MountRecoveryError(
                "Mount evidence reader returned invalid identities"
            )
        ids = [identity.mount_id for identity in identities]
        if len(ids) != len(set(ids)):
            raise MountRecoveryError(
                "Mount evidence contains duplicate identifiers"
            )
        return identities

    def _query_x_state(self):
        result = self._x_query()
        if not isinstance(result, mounts.XAccessState):
            raise mounts.XAccessEvidenceError(
                "X query returned invalid evidence"
            )
        return result

    def _mutate_x(self, add):
        return bool(self._x_mutator(add))

    def _request_from_plan(self, plan):
        return mounts.MountRequest(
            source=plan["source"],
            destination=plan["destination"],
            label=plan["label"],
            options=tuple(plan["options"]),
            recursive=plan["recursive"],
            kind=plan["kind"],
            custody=plan["custody"],
        )

    def _validate_mount_destination(self, request):
        if request.kind == "iso":
            return mounts.validate_iso_custody(request)
        return mounts.validate_mount_destination(
            self.ctx,
            request.destination,
        )

    def _is_persisted_iso_destination(self, destination):
        return any(
            plan.get("kind") == "iso"
            and plan["destination"] == destination
            for plan in self._state["mounts"]
        )

    def _request_is_authorized(self, request):
        if request.kind == "iso":
            try:
                mounts.validate_iso_custody(request)
            except mounts.MountEvidenceError:
                return False
            return True
        return any(
            self._request_signature(request)
            == self._request_signature(candidate)
            for candidate in mounts.authorized_mount_requests(
                self.ctx
            )
        )

    @staticmethod
    def _request_signature(request):
        return (
            os.path.normpath(request.source),
            os.path.normpath(request.destination),
            request.label,
            tuple(request.options),
            request.recursive,
            request.kind,
        )

    def _initialize_journal(self):
        if os.path.lexists(self.journal_path):
            raise MountRecoveryError(
                "A mount-session journal already exists"
            )
        if self._pending_journal_paths():
            raise MountRecoveryError(
                "Pending mount-session journal metadata exists"
            )
        self._state = {
            "artifacts": [],
            "directories": [],
            "mounts": [],
            "owner": {
                "pid": os.getpid(),
                "token": uuid.uuid4().hex,
            },
            "phase": "active",
            "previous_sha256": None,
            "roots": {
                "filesystem": self.fs_root,
                "work": self.work_root,
            },
            "sequence": 0,
            "version": _JOURNAL_VERSION,
            "x": {
                "before": None,
                "mutation": False,
                "stage": "unexamined",
            },
        }
        self._journal_active = False
        self._cleanup_attempted = False
        self._persist_journal()

    def _recover_existing_transaction(self):
        self._reconcile_pending_journal()
        if not os.path.lexists(self.journal_path):
            return
        self._load_existing_journal()
        self._recovering = True
        try:
            self.cleanup()
        finally:
            self._recovering = False
        self._state = None
        self._persisted_state = None
        self._journal_active = False

    def _load_existing_journal(self):
        self._state = self._read_journal()
        self._validate_state(self._state)
        self._persisted_state = copy.deepcopy(self._state)
        self._journal_active = True

    def _persist_journal(self):
        if self._lock_descriptor is None or self._state is None:
            raise MountRecoveryError(
                "Journal persistence requires the runtime lock"
            )
        self._validate_runtime_lock_identity()
        if self._pending_journal_paths():
            raise MountRecoveryError(
                "Pending journal metadata is ambiguous"
            )
        previous_digest = None
        if self._journal_active:
            current, current_raw = self._read_journal_with_raw()
            if current != self._persisted_state:
                raise MountRecoveryError(
                    "Mount-session journal identity changed"
                )
            previous_digest = hashlib.sha256(
                current_raw
            ).hexdigest()
        elif os.path.lexists(self.journal_path):
            raise MountRecoveryError(
                "Unexpected mount-session journal exists"
            )
        self._validate_state(self._state)
        next_state = copy.deepcopy(self._state)
        next_state["sequence"] += 1
        next_state["previous_sha256"] = previous_digest
        self._validate_state(next_state)
        raw = self._encode_json(next_state)
        if len(raw) > _MAX_METADATA_BYTES:
            raise MountRecoveryError(
                "Mount-session journal exceeds the writer limit"
            )
        pending_path = (
            self.pending_prefix
            + next_state["owner"]["token"]
            + "-"
            + uuid.uuid4().hex
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(pending_path, flags, 0o600)
        try:
            self._validate_secure_file_state(
                os.fstat(descriptor),
                "Pending journal",
            )
            self._write_all(descriptor, raw)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        pending_state = os.lstat(pending_path)
        self._validate_secure_file_state(
            pending_state,
            "Pending journal",
        )
        os.replace(pending_path, self.journal_path)
        self._fsync_directory(self.runtime_dir)
        self._state["sequence"] = next_state["sequence"]
        self._state["previous_sha256"] = previous_digest
        self._persisted_state = copy.deepcopy(self._state)
        self._journal_active = True

    def _remove_journal(self):
        self._validate_runtime_lock_identity()
        if self._pending_journal_paths():
            raise MountRecoveryError(
                "Pending journal metadata prevents finalization"
            )
        current = self._read_journal()
        if current != self._state or current["phase"] != "complete":
            raise MountRecoveryError(
                "Mount-session journal cannot be finalized"
            )
        os.unlink(self.journal_path)
        self._fsync_directory(self.runtime_dir)
        self._journal_active = False
        self._persisted_state = None

    def _read_journal(self):
        value, _raw = self._read_journal_with_raw()
        return value

    def _read_journal_with_raw(self):
        return self._read_metadata_file(
            self.journal_path,
            "Mount-session journal",
        )

    def _read_metadata_file(self, path, label):
        stat_result = os.lstat(path)
        self._validate_secure_file_state(
            stat_result,
            label,
        )
        if stat_result.st_size > _MAX_METADATA_BYTES:
            raise MountRecoveryError(
                f"{label} is too large"
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            descriptor_state = os.fstat(descriptor)
            self._validate_secure_file_state(
                descriptor_state,
                label,
            )
            if (
                descriptor_state.st_dev != stat_result.st_dev
                or descriptor_state.st_ino != stat_result.st_ino
                or descriptor_state.st_size
                != stat_result.st_size
            ):
                raise MountRecoveryError(
                    f"{label} identity changed"
                )
            chunks = []
            total = 0
            while True:
                chunk = os.read(
                    descriptor,
                    min(
                        1024 * 1024,
                        _MAX_METADATA_BYTES + 1 - total,
                    ),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_METADATA_BYTES:
                    break
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_METADATA_BYTES:
            raise MountRecoveryError(
                f"{label} is too large"
            )
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MountRecoveryError(
                f"{label} is corrupt"
            ) from error
        return value, raw

    def _reconcile_pending_journal(self):
        self._validate_runtime_lock_identity()
        pending_paths = self._pending_journal_paths()
        if not pending_paths:
            return
        if len(pending_paths) != 1:
            raise MountRecoveryError(
                "Pending journal evidence is multiple or ambiguous"
            )
        pending_path = pending_paths[0]
        candidate, _candidate_raw = self._read_metadata_file(
            pending_path,
            "Pending journal",
        )
        self._validate_state(candidate)
        if not os.path.lexists(self.journal_path):
            self._validate_initial_pending_journal(
                candidate,
                pending_path,
            )
            os.replace(pending_path, self.journal_path)
            self._fsync_directory(self.runtime_dir)
            return
        current, current_raw = self._read_journal_with_raw()
        self._validate_state(current)
        self._validate_pending_transition(
            current,
            current_raw,
            candidate,
            pending_path,
        )
        os.replace(pending_path, self.journal_path)
        self._fsync_directory(self.runtime_dir)

    def _validate_initial_pending_journal(
        self,
        candidate,
        pending_path,
    ):
        self._validate_pending_filename(
            candidate["owner"]["token"],
            pending_path,
        )
        if (
            candidate["sequence"] != 1
            or candidate["previous_sha256"] is not None
            or candidate["phase"] != "active"
            or candidate["mounts"]
            or candidate["directories"]
            or candidate["artifacts"]
            or candidate["x"]
            != {
                "before": None,
                "mutation": False,
                "stage": "unexamined",
            }
        ):
            raise MountRecoveryError(
                "Predecessorless pending journal is not an initial state"
            )

    def _validate_pending_filename(self, token, pending_path):
        prefix = (
            os.path.basename(self.pending_prefix)
            + token
            + "-"
        )
        name = os.path.basename(pending_path)
        suffix = name[len(prefix):] if name.startswith(prefix) else ""
        if not self._is_hex(suffix, 32):
            raise MountRecoveryError(
                "Pending journal filename is not in transaction custody"
            )

    def _validate_pending_transition(
        self,
        current,
        current_raw,
        candidate,
        pending_path,
    ):
        self._validate_pending_filename(
            current["owner"]["token"],
            pending_path,
        )
        expected_previous = hashlib.sha256(
            current_raw
        ).hexdigest()
        if (
            candidate["version"] != current["version"]
            or candidate["owner"] != current["owner"]
            or candidate["roots"] != current["roots"]
            or candidate["sequence"] != current["sequence"] + 1
            or candidate["previous_sha256"] != expected_previous
        ):
            raise MountRecoveryError(
                "Pending journal does not prove the next sequence"
            )
        allowed_phases = {
            "active": {"active", "cleaning"},
            "cleaning": {"cleaning", "complete"},
            "complete": set(),
        }
        if candidate["phase"] not in allowed_phases[current["phase"]]:
            raise MountRecoveryError(
                "Pending journal phase transition is invalid"
            )
        added = 0
        for field in ("mounts", "directories", "artifacts"):
            previous_records = current[field]
            next_records = candidate[field]
            if (
                len(next_records) < len(previous_records)
                or len(next_records) > len(previous_records) + 1
            ):
                raise MountRecoveryError(
                    "Pending journal resource transition is invalid"
                )
            added += len(next_records) - len(previous_records)
        if added > 1:
            raise MountRecoveryError(
                "Pending journal appends multiple resources"
            )
        self._validate_pending_mount_transitions(
            current["mounts"],
            candidate["mounts"],
        )
        self._validate_pending_directory_transitions(
            current["directories"],
            candidate["directories"],
        )
        self._validate_pending_artifact_transitions(
            current["artifacts"],
            candidate["artifacts"],
        )
        self._validate_pending_x_transition(
            current["x"],
            candidate["x"],
        )
        if added and (
            candidate["phase"] != current["phase"]
            or candidate["x"] != current["x"]
            or any(
                candidate[field][
                    :len(current[field])
                ] != current[field]
                for field in (
                    "mounts",
                    "directories",
                    "artifacts",
                )
            )
        ):
            raise MountRecoveryError(
                "Pending journal append changes existing state"
            )

    def _validate_pending_mount_transitions(
        self,
        current_records,
        candidate_records,
    ):
        for previous, next_record in zip(
            current_records,
            candidate_records,
        ):
            stable = (
                "before",
                "custody",
                "destination",
                "id",
                "kind",
                "label",
                "options",
                "recursive",
                "source",
            )
            if any(
                previous[field] != next_record[field]
                for field in stable
            ):
                raise MountRecoveryError(
                    "Pending journal changes immutable mount evidence"
                )
            allowed = {
                "planned": {
                    "planned",
                    "command-started",
                    "failed",
                    "ambiguous",
                },
                "command-started": {
                    "command-started",
                    "owned",
                    "failed",
                    "ambiguous",
                },
                "owned": {"owned"},
                "already-present": {"already-present"},
                "failed": {"failed"},
                "ambiguous": {"ambiguous", "failed"},
            }
            if next_record["stage"] not in allowed[previous["stage"]]:
                raise MountRecoveryError(
                    "Pending journal mount stage transition is invalid"
                )
            observation_may_change = (
                previous["stage"] == "planned"
                and next_record["stage"] == "ambiguous"
            ) or (
                previous["stage"] == "command-started"
                and next_record["stage"]
                in {"owned", "failed", "ambiguous"}
            )
            if (
                not observation_may_change
                and next_record["observed_after"]
                != previous["observed_after"]
            ):
                raise MountRecoveryError(
                    "Pending journal mount observation transition "
                    "is invalid"
                )
            self._validate_pending_owned_transitions(
                previous,
                next_record,
            )
        for appended in candidate_records[len(current_records):]:
            if (
                appended["stage"] not in {
                    "planned",
                    "already-present",
                }
                or appended["owned"]
                or (
                    appended["stage"] == "planned"
                    and appended["observed_after"]
                )
                or (
                    appended["stage"] == "already-present"
                    and appended["observed_after"]
                    != appended["before"]
                )
            ):
                raise MountRecoveryError(
                    "Pending journal appended mount is not initial"
                )

    def _validate_pending_owned_transitions(
        self,
        previous,
        candidate,
    ):
        previous_owned = previous["owned"]
        next_owned = candidate["owned"]
        if previous["stage"] == "command-started":
            if candidate["stage"] == "owned":
                if any(
                    owned["stage"] != "owned"
                    for owned in next_owned
                ):
                    raise MountRecoveryError(
                        "Pending journal initial mount ownership "
                        "is invalid"
                    )
                if any(owned["inferred"] for owned in next_owned):
                    self._validate_inferred_recovery_delta(
                        previous,
                        candidate,
                    )
                elif not next_owned:
                    raise MountRecoveryError(
                        "Pending journal initial mount ownership "
                        "is empty"
                    )
            elif next_owned:
                raise MountRecoveryError(
                    "Pending journal adopts mount ownership"
                )
            return
        if previous["stage"] != "owned":
            if next_owned != previous_owned:
                raise MountRecoveryError(
                    "Pending journal changes unowned mount identities"
                )
            return
        if len(next_owned) < len(previous_owned):
            raise MountRecoveryError(
                "Pending journal removes owned mount identities"
            )
        allowed = {
            "owned": {"owned", "unmounting"},
            "unmounting": {"unmounting", "removed"},
            "removed": {"removed"},
        }
        for old, new in zip(previous_owned, next_owned):
            if (
                old["identity"] != new["identity"]
                or old["inferred"] != new["inferred"]
                or new["stage"] not in allowed[old["stage"]]
            ):
                raise MountRecoveryError(
                    "Pending journal owned-mount transition is invalid"
                )
        for appended in next_owned[len(previous_owned):]:
            if (
                not appended["inferred"]
                or appended["stage"] != "owned"
            ):
                raise MountRecoveryError(
                    "Pending journal inferred mount is invalid"
                )

    def _validate_inferred_recovery_delta(self, previous, candidate):
        before = {
            mounts.MountIdentity.from_record(record).key
            for record in previous["before"]
        }
        observed = {
            mounts.MountIdentity.from_record(record).key
            for record in candidate["observed_after"]
        }
        owned = {
            mounts.MountIdentity.from_record(
                record["identity"]
            ).key
            for record in candidate["owned"]
        }
        if (
            not owned
            or not before.issubset(observed)
            or owned != observed - before
            or any(
                not record["inferred"]
                or record["stage"] != "owned"
                for record in candidate["owned"]
            )
        ):
            raise MountRecoveryError(
                "Pending inferred ownership is not the exact mount delta"
            )

    def _validate_pending_directory_transitions(
        self,
        current_records,
        candidate_records,
    ):
        for previous, next_record in zip(
            current_records,
            candidate_records,
        ):
            if (
                any(
                    previous[field] != next_record[field]
                    for field in (
                        "desired_mode",
                        "parent_identity",
                        "path",
                        "staging_path",
                    )
                )
            ):
                raise MountRecoveryError(
                    "Pending journal changes directory custody"
                )
            allowed = {
                "planned": {"planned", "staged", "removed"},
                "staged": {"staged", "rename-planned", "removing"},
                "rename-planned": {
                    "rename-planned",
                    "created",
                    "removing",
                },
                "created": {"created", "removing"},
                "removing": {"removing", "removed"},
                "removed": {"removed"},
            }
            if next_record["stage"] not in allowed[previous["stage"]]:
                raise MountRecoveryError(
                    "Pending journal directory transition is invalid"
                )
            if previous["stage"] == "planned":
                if (
                    next_record["stage"] == "staged"
                    and (
                        previous["identity"] is not None
                        or previous["staging_identity"] is not None
                        or next_record["staging_identity"] is None
                    )
                ) or (
                    next_record["stage"] != "staged"
                    and (
                        next_record["identity"] != previous["identity"]
                        or next_record["staging_identity"]
                        != previous["staging_identity"]
                    )
                ):
                    raise MountRecoveryError(
                        "Pending journal directory identity transition "
                        "is invalid"
                    )
            elif (
                previous["stage"] == "rename-planned"
                and next_record["stage"] == "created"
            ):
                if (
                    previous["identity"] is not None
                    or next_record["identity"]
                    != previous["staging_identity"]
                    or next_record["staging_identity"]
                    != previous["staging_identity"]
                ):
                    raise MountRecoveryError(
                        "Pending journal renamed directory identity "
                        "is invalid"
                    )
            elif (
                next_record["identity"] != previous["identity"]
                or next_record["staging_identity"]
                != previous["staging_identity"]
            ):
                raise MountRecoveryError(
                    "Pending journal changes directory identity"
                )
        for appended in candidate_records[len(current_records):]:
            if (
                appended["stage"] != "planned"
                or appended["identity"] is not None
                or appended["staging_identity"] is not None
            ):
                raise MountRecoveryError(
                    "Pending journal appended directory is not initial"
                )

    def _validate_pending_artifact_transitions(
        self,
        current_records,
        candidate_records,
    ):
        for previous, next_record in zip(
            current_records,
            candidate_records,
        ):
            stable = ("expected", "path", "purpose")
            if any(
                previous[field] != next_record[field]
                for field in stable
            ):
                raise MountRecoveryError(
                    "Pending journal changes artifact custody"
                )
            allowed = {
                "planned": {"planned", "writing", "removed"},
                "writing": {
                    "writing",
                    "active",
                    "removing",
                },
                "active": {"active", "removing"},
                "removing": {"removing", "removed"},
                "removed": {"removed"},
            }
            if next_record["stage"] not in allowed[previous["stage"]]:
                raise MountRecoveryError(
                    "Pending journal artifact transition is invalid"
                )
            if previous["stage"] == "planned":
                if (
                    next_record["stage"] == "writing"
                    and (
                        previous["identity"] is not None
                        or next_record["identity"] is None
                    )
                ) or (
                    next_record["stage"] != "writing"
                    and next_record["identity"]
                    != previous["identity"]
                ):
                    raise MountRecoveryError(
                        "Pending journal artifact identity transition "
                        "is invalid"
                    )
            elif (
                previous["stage"] == "writing"
                and next_record["stage"] in {"active", "removing"}
            ):
                if not self._same_file_node(
                    previous["identity"],
                    next_record["identity"],
                ):
                    raise MountRecoveryError(
                        "Pending journal artifact inode transition "
                        "is invalid"
                    )
            elif next_record["identity"] != previous["identity"]:
                raise MountRecoveryError(
                    "Pending journal changes artifact identity"
                )
        for appended in candidate_records[len(current_records):]:
            if (
                appended["stage"] != "planned"
                or appended["identity"] is not None
            ):
                raise MountRecoveryError(
                    "Pending journal appended artifact is not initial"
                )

    def _validate_pending_x_transition(self, previous, candidate):
        allowed = {
            "unexamined": {
                "unexamined",
                "no-change",
                "grant-planned",
            },
            "no-change": {"no-change", "grant-planned"},
            "grant-planned": {
                "grant-planned",
                "owned",
                "failed",
            },
            "owned": {"owned", "revoking"},
            "revoking": {"revoking", "restored"},
            "restored": {"restored"},
            "failed": {"failed"},
        }
        if candidate["stage"] not in allowed[previous["stage"]]:
            raise MountRecoveryError(
                "Pending journal X stage transition is invalid"
            )
        if previous["stage"] == "unexamined":
            if candidate["stage"] == "unexamined":
                if candidate != previous:
                    raise MountRecoveryError(
                        "Pending journal changes untouched X state"
                    )
            elif (
                previous["before"] is not None
                or candidate["before"] is None
            ):
                raise MountRecoveryError(
                    "Pending journal X pre-state transition is invalid"
                )
        elif (
            previous["stage"] == "no-change"
            and candidate["stage"] == "grant-planned"
        ):
            if candidate["before"] is None:
                raise MountRecoveryError(
                    "Pending journal X pre-state transition is invalid"
                )
        elif (
            previous["stage"] == "no-change"
            and candidate["stage"] == "no-change"
        ):
            if (
                candidate["before"] is None
                or candidate["mutation"]
            ):
                raise MountRecoveryError(
                    "Pending journal X no-change refresh is invalid"
                )
        elif candidate["before"] != previous["before"]:
            raise MountRecoveryError(
                "Pending journal changes X pre-state"
            )

    def _validate_state(self, state):
        expected = {
            "artifacts",
            "directories",
            "mounts",
            "owner",
            "phase",
            "previous_sha256",
            "roots",
            "sequence",
            "version",
            "x",
        }
        if (
            not isinstance(state, dict)
            or set(state) != expected
            or state["version"] != _JOURNAL_VERSION
            or type(state["sequence"]) is not int
            or state["sequence"] < 0
            or state["phase"]
            not in {"active", "cleaning", "complete"}
            or (
                state["sequence"] <= 1
                and state["previous_sha256"] is not None
            )
            or (
                state["sequence"] > 1
                and not self._is_hex(
                    state["previous_sha256"],
                    64,
                )
            )
        ):
            raise MountRecoveryError(
                "Mount-session journal schema is invalid"
            )
        self._validate_owner(state["owner"])
        if state["roots"] != {
            "filesystem": self.fs_root,
            "work": self.work_root,
        }:
            raise MountRecoveryError(
                "Mount-session journal roots do not match context"
            )
        if not all(
            isinstance(state[field], list)
            for field in ("artifacts", "directories", "mounts")
        ):
            raise MountRecoveryError(
                "Mount-session resource arrays are invalid"
            )
        self._validate_mount_records(state["mounts"])
        self._validate_artifact_records(
            state["artifacts"],
            state["owner"]["token"],
        )
        self._validate_directory_records(
            state["directories"],
            state["mounts"],
            state["artifacts"],
        )
        self._validate_x_record(state["x"])

    def _validate_owner(self, owner):
        if (
            not isinstance(owner, dict)
            or set(owner) != {"pid", "token"}
            or type(owner["pid"]) is not int
            or owner["pid"] < 1
            or not self._is_hex(owner["token"], 32)
        ):
            raise MountRecoveryError(
                "Mount-session owner metadata is invalid"
            )

    def _validate_mount_records(self, records):
        plan_ids = set()
        owned_keys = set()
        for plan in records:
            expected = {
                "before",
                "custody",
                "destination",
                "id",
                "kind",
                "label",
                "observed_after",
                "options",
                "owned",
                "recursive",
                "source",
                "stage",
            }
            if (
                not isinstance(plan, dict)
                or set(plan) != expected
                or not self._is_hex(plan["id"], 32)
                or plan["id"] in plan_ids
                or plan["stage"] not in _MOUNT_PLAN_STAGES
                or plan["kind"] not in {"filesystem", "iso"}
                or type(plan["recursive"]) is not bool
                or not isinstance(plan["source"], str)
                or not os.path.isabs(plan["source"])
                or os.path.normpath(plan["source"])
                != plan["source"]
                or not isinstance(plan["label"], str)
                or not plan["label"]
                or not isinstance(plan["options"], list)
                or any(
                    not isinstance(option, str)
                    for option in plan["options"]
                )
                or not isinstance(plan["before"], list)
                or not isinstance(plan["observed_after"], list)
                or not isinstance(plan["owned"], list)
            ):
                raise MountRecoveryError(
                    "Mount plan metadata is invalid"
                )
            plan_ids.add(plan["id"])
            request = self._request_from_plan(plan)
            try:
                destination = self._validate_mount_destination(
                    request
                )
            except mounts.MountEvidenceError as error:
                raise MountRecoveryError(
                    "Mount plan destination is invalid"
                ) from error
            if not self._request_is_authorized(request):
                raise MountRecoveryError(
                    "Mount plan is outside the authorized plan set"
                )
            if (
                plan["stage"] == "owned"
                and not plan["owned"]
            ) or (
                plan["stage"] != "owned"
                and plan["owned"]
            ):
                raise MountRecoveryError(
                    "Mount plan ownership stage is invalid"
                )
            identity_lists = {}
            for field in ("before", "observed_after"):
                field_keys = set()
                for identity_record in plan[field]:
                    identity = mounts.MountIdentity.from_record(
                        identity_record
                    )
                    if identity.key in field_keys:
                        raise MountRecoveryError(
                            "Mount evidence identity is duplicated"
                        )
                    field_keys.add(identity.key)
                    if not self._path_within(
                        destination,
                        identity.mount_point,
                        include_root=True,
                    ):
                        raise MountRecoveryError(
                            "Mount evidence escapes its plan"
                        )
                identity_lists[field] = field_keys
            for owned in plan["owned"]:
                if (
                    not isinstance(owned, dict)
                    or set(owned)
                    != {"identity", "inferred", "stage"}
                    or type(owned["inferred"]) is not bool
                    or owned["stage"] not in _MOUNT_IDENTITY_STAGES
                ):
                    raise MountRecoveryError(
                        "Owned mount metadata is invalid"
                    )
                identity = mounts.MountIdentity.from_record(
                    owned["identity"]
                )
                if identity.key in owned_keys:
                    raise MountRecoveryError(
                        "Owned mount identity is duplicated"
                    )
                owned_keys.add(identity.key)
                if (
                    not owned["inferred"]
                    and identity.key
                    not in identity_lists["observed_after"]
                ):
                    raise MountRecoveryError(
                        "Owned mount lacks observed acquisition evidence"
                    )
                if not self._path_within(
                    destination,
                    identity.mount_point,
                    include_root=True,
                ):
                    raise MountRecoveryError(
                        "Owned mount identity escapes its plan"
                    )

    def _validate_directory_records(
        self,
        records,
        mount_records,
        artifact_records,
    ):
        paths = set()
        resource_targets = [
            os.path.join(self.fs_root, "tmp"),
        ]
        resource_targets.extend(
            record["destination"]
            for record in mount_records
        )
        resource_targets.extend(
            os.path.dirname(record["path"])
            for record in artifact_records
        )
        for record in records:
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "desired_mode",
                    "identity",
                    "parent_identity",
                    "path",
                    "stage",
                    "staging_identity",
                    "staging_path",
                }
                or record["stage"] not in _DIRECTORY_STAGES
                or record["path"] in paths
                or type(record["desired_mode"]) is not int
                or record["desired_mode"] not in {0o700, 0o755}
            ):
                raise MountRecoveryError(
                    "Created-directory metadata is invalid"
                )
            iso_plan = next(
                (
                    plan
                    for plan in mount_records
                    if plan.get("kind") == "iso"
                    and plan["destination"] == record["path"]
                ),
                None,
            )
            if iso_plan is None:
                path = self._validate_resource_path(record["path"])
            else:
                try:
                    path = mounts.validate_iso_custody(
                        self._request_from_plan(iso_plan)
                    )
                except mounts.MountEvidenceError as error:
                    raise MountRecoveryError(
                        "Journal ISO directory path is invalid: "
                        f"{record['path']}"
                    ) from error
            paths.add(path)
            staging_path = record["staging_path"]
            expected_prefix = (
                "." + os.path.basename(path) + ".liveusb-dir-"
            )
            suffix = (
                os.path.basename(staging_path)[len(expected_prefix):]
                if os.path.basename(staging_path).startswith(
                    expected_prefix
                )
                else ""
            )
            if (
                not isinstance(staging_path, str)
                or not os.path.isabs(staging_path)
                or os.path.normpath(staging_path) != staging_path
                or os.path.dirname(staging_path) != os.path.dirname(path)
                or not self._is_hex(suffix, 32)
            ):
                raise MountRecoveryError(
                    "Staging-directory custody is invalid"
                )
            if not any(
                self._path_within(
                    path,
                    target,
                    include_root=True,
                )
                for target in resource_targets
            ):
                raise MountRecoveryError(
                    "Created directory has no owned resource target"
                )
            self._validate_directory_identity(
                record["parent_identity"]
            )
            if record["identity"] is not None:
                self._validate_directory_identity(record["identity"])
            if record["staging_identity"] is not None:
                self._validate_directory_identity(
                    record["staging_identity"]
                )
            if (
                record["stage"] == "planned"
                and (
                    record["identity"] is not None
                    or record["staging_identity"] is not None
                )
            ) or (
                record["stage"] in {"staged", "rename-planned"}
                and (
                    record["identity"] is not None
                    or record["staging_identity"] is None
                )
            ) or (
                record["stage"] == "created"
                and (
                    record["identity"] is None
                    or record["identity"]
                    != record["staging_identity"]
                )
            ):
                raise MountRecoveryError(
                    "Directory transaction identity state is invalid"
                )
            if (
                record["stage"] in {"created", "removing"}
                and record["identity"] is None
                and record["staging_identity"] is None
            ):
                raise MountRecoveryError(
                    "Active directory identity is missing"
                )

    def _validate_artifact_records(self, records, token):
        paths = set()
        for record in records:
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "expected",
                    "identity",
                    "path",
                    "purpose",
                    "stage",
                }
                or record["stage"] not in _ARTIFACT_STAGES
                or not isinstance(record["purpose"], str)
                or not record["purpose"]
                or record["path"] in paths
            ):
                raise MountRecoveryError(
                    "Staged-artifact metadata is invalid"
                )
            path = self._validate_resource_path(record["path"])
            paths.add(path)
            expected_parent = os.path.join(self.fs_root, "tmp")
            expected_prefix = (
                f"liveusb-{record['purpose']}-{token}-"
            )
            if (
                os.path.dirname(path) != expected_parent
                or not os.path.basename(path).startswith(
                    expected_prefix
                )
            ):
                raise MountRecoveryError(
                    "Staged artifact is outside its custody namespace"
                )
            self._validate_expected_file(record["expected"])
            if record["identity"] is not None:
                self._validate_file_identity(record["identity"])
            if (
                record["stage"] in {
                    "writing",
                    "active",
                    "removing",
                }
                and record["identity"] is None
            ):
                raise MountRecoveryError(
                    "Active artifact identity is missing"
                )

    def _validate_x_record(self, record):
        if (
            not isinstance(record, dict)
            or set(record) != {"before", "mutation", "stage"}
            or record["stage"] not in _X_STAGES
            or type(record["mutation"]) is not bool
        ):
            raise MountRecoveryError(
                "X lifecycle metadata is invalid"
            )
        if record["before"] is not None:
            mounts.XAccessState.from_record(record["before"])
        if (
            record["stage"]
            in {"grant-planned", "owned", "revoking"}
            and record["before"] is None
        ):
            raise MountRecoveryError(
                "X lifecycle pre-state is missing"
            )
        expected_mutation = record["stage"] in {
            "owned",
            "revoking",
        }
        if record["mutation"] != expected_mutation:
            raise MountRecoveryError(
                "X lifecycle mutation state is invalid"
            )

    @staticmethod
    def _validate_directory_identity(value):
        if (
            not isinstance(value, dict)
            or set(value) != {"dev", "ino", "kind", "mode"}
            or value["kind"] != "directory"
            or any(
                type(value[field]) is not int
                or value[field] < 0
                for field in ("dev", "ino", "mode")
            )
        ):
            raise MountRecoveryError(
                "Directory identity metadata is invalid"
            )

    @staticmethod
    def _validate_expected_file(value):
        if (
            not isinstance(value, dict)
            or set(value) != {"mode", "sha256", "size"}
            or type(value["mode"]) is not int
            or value["mode"] < 0
            or type(value["size"]) is not int
            or value["size"] < 0
            or not MountSession._is_hex(value["sha256"], 64)
        ):
            raise MountRecoveryError(
                "Expected artifact metadata is invalid"
            )

    @staticmethod
    def _validate_file_identity(value):
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "dev",
                "ino",
                "kind",
                "mode",
                "nlink",
                "owner",
                "sha256",
                "size",
            }
            or value["kind"] != "file"
            or any(
                type(value[field]) is not int
                or value[field] < 0
                for field in (
                    "dev",
                    "ino",
                    "mode",
                    "nlink",
                    "owner",
                    "size",
                )
            )
            or value["nlink"] != 1
            or value["owner"] != os.geteuid()
            or not MountSession._is_hex(value["sha256"], 64)
        ):
            raise MountRecoveryError(
                "Artifact identity metadata is invalid"
            )

    def _validate_resource_path(self, path):
        if (
            not isinstance(path, str)
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
            or not self._path_within(
                self.fs_root,
                path,
                include_root=False,
            )
        ):
            raise MountRecoveryError(
                f"Journal path escapes FileSystem: {path}"
            )
        parent_real = os.path.realpath(os.path.dirname(path))
        if not self._path_within(
            self.fs_root,
            parent_real,
            include_root=True,
        ):
            raise MountRecoveryError(
                f"Journal path parent escapes FileSystem: {path}"
            )
        if os.path.lexists(path) and os.path.islink(path):
            raise MountRecoveryError(
                f"Journal path is a symbolic link: {path}"
            )
        return path

    def _validate_directory_resource_path(self, path):
        if (
            isinstance(path, str)
            and os.path.isabs(path)
            and os.path.normpath(path) == path
            and self._path_within(
                self.fs_root,
                path,
                include_root=False,
            )
        ):
            return self._validate_resource_path(path)
        if (
            not isinstance(path, str)
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
            or not self._is_persisted_iso_destination(path)
        ):
            raise MountRecoveryError(
                f"Journal directory path is outside custody: {path}"
            )
        try:
            plan = next(
                plan
                for plan in self._state["mounts"]
                if plan.get("kind") == "iso"
                and plan["destination"] == path
            )
            return mounts.validate_iso_custody(
                self._request_from_plan(plan)
            )
        except mounts.MountEvidenceError as error:
            raise MountRecoveryError(
                f"Journal ISO directory path is invalid: {path}"
            ) from error

    def _acquire_runtime_lock(self):
        if self._lock_descriptor is not None:
            return
        self._ensure_runtime_directory()
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            stat_result = os.fstat(descriptor)
            self._validate_secure_file_state(
                stat_result,
                "Runtime lock",
            )
            path_result = os.lstat(self.lock_path)
            self._validate_secure_file_state(
                path_result,
                "Runtime lock",
            )
            if (
                stat_result.st_dev != path_result.st_dev
                or stat_result.st_ino != path_result.st_ino
            ):
                raise MountRecoveryError(
                    "Runtime lock identity changed during acquisition"
                )
            try:
                fcntl.flock(
                    descriptor,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as error:
                raise MountRecoveryError(
                    "Another LiveUSB operation holds the runtime lock"
                ) from error
            self._lock_descriptor = descriptor
            self._lock_identity = (
                stat_result.st_dev,
                stat_result.st_ino,
            )
            descriptor = None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _ensure_runtime_directory(self):
        if (
            not os.path.isabs(self.runtime_dir)
            or os.path.normpath(self.runtime_dir) != self.runtime_dir
            or self._path_within(
                self.work_root,
                self.runtime_dir,
                include_root=True,
            )
        ):
            raise MountRecoveryError(
                "Runtime custody path is invalid"
            )
        parent = os.path.dirname(self.runtime_dir)
        self._validate_runtime_parent_chain(parent)
        if not os.path.lexists(self.runtime_dir):
            try:
                parent_state = os.lstat(parent)
            except OSError as error:
                raise MountRecoveryError(
                    "Runtime custody parent is unavailable"
                ) from error
            if not stat.S_ISDIR(parent_state.st_mode):
                raise MountRecoveryError(
                    "Runtime parent changed before leaf creation"
                )
            try:
                os.mkdir(self.runtime_dir, 0o700)
            except OSError as error:
                raise MountRecoveryError(
                    "Unable to create runtime custody directory"
                ) from error
            self._fsync_directory(parent)
        try:
            stat_result = os.lstat(self.runtime_dir)
        except OSError as error:
            raise MountRecoveryError(
                "Unable to inspect runtime custody directory"
            ) from error
        if (
            not stat.S_ISDIR(stat_result.st_mode)
            or stat.S_ISLNK(stat_result.st_mode)
            or os.path.realpath(self.runtime_dir)
            != self.runtime_dir
            or stat_result.st_uid != os.geteuid()
            or stat.S_IMODE(stat_result.st_mode) != 0o700
        ):
            raise MountRecoveryError(
                "Runtime custody directory is invalid"
            )

    def _validate_runtime_parent_chain(self, parent):
        components = parent.split(os.sep)
        cursor = os.sep
        chain = [os.sep]
        for component in components:
            if not component:
                continue
            cursor = os.path.join(cursor, component)
            chain.append(cursor)
        for cursor in chain:
            try:
                state = os.lstat(cursor)
            except OSError as error:
                raise MountRecoveryError(
                    "Runtime custody parent chain is incomplete"
                ) from error
            if (
                not stat.S_ISDIR(state.st_mode)
                or stat.S_ISLNK(state.st_mode)
            ):
                raise MountRecoveryError(
                    "Runtime parent chain is not a literal directory chain"
                )
            mode = stat.S_IMODE(state.st_mode)
            if (
                mode & 0o022
                and not (state.st_mode & stat.S_ISVTX)
            ):
                raise MountRecoveryError(
                    "Runtime parent chain contains an unsafe "
                    "writable ancestor"
                )

    def _release_runtime_lock(self):
        if self._lock_descriptor is None:
            return
        descriptor = self._lock_descriptor
        self._lock_descriptor = None
        self._lock_identity = None
        unlock_error = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as error:
            unlock_error = error
        try:
            os.close(descriptor)
        except OSError as close_error:
            if unlock_error is not None:
                raise close_error from unlock_error
            raise
        if unlock_error is not None:
            raise unlock_error

    def _validate_runtime_lock_identity(self):
        if self._lock_descriptor is None or self._lock_identity is None:
            raise MountRecoveryError(
                "Runtime lock ownership is unavailable"
            )
        if not os.path.lexists(self.lock_path):
            raise MountRecoveryError(
                "Runtime lock path is absent"
            )
        path_state = os.lstat(self.lock_path)
        descriptor_state = os.fstat(self._lock_descriptor)
        path_identity = (
            path_state.st_dev,
            path_state.st_ino,
        )
        descriptor_identity = (
            descriptor_state.st_dev,
            descriptor_state.st_ino,
        )
        if (
            path_identity != self._lock_identity
            or descriptor_identity != self._lock_identity
        ):
            raise MountRecoveryError(
                "Runtime lock identity changed"
            )
        self._validate_secure_file_state(
            path_state,
            "Runtime lock",
        )
        self._validate_secure_file_state(
            descriptor_state,
            "Runtime lock",
        )

    @staticmethod
    def _validate_secure_file_state(state, label):
        if (
            not stat.S_ISREG(state.st_mode)
            or state.st_uid != os.geteuid()
            or stat.S_IMODE(state.st_mode) != 0o600
            or state.st_nlink != 1
        ):
            raise MountRecoveryError(
                f"{label} custody is invalid"
            )

    def _release_runtime_lock_quietly(self):
        try:
            self._release_runtime_lock()
        except OSError:
            pass

    def _pending_journal_paths(self):
        if not os.path.isdir(self.runtime_dir):
            return tuple()
        prefix = os.path.basename(self.pending_prefix)
        return tuple(
            sorted(
                os.path.join(self.runtime_dir, name)
                for name in os.listdir(self.runtime_dir)
                if name.startswith(prefix)
            )
        )

    def _require_active_session(self):
        if (
            not self._entered
            or self._lock_descriptor is None
            or not self._journal_active
            or self._state is None
        ):
            raise MountAcquisitionError(
                "Mount session is not active"
            )

    @staticmethod
    def _attempt_cleanup(
        failures,
        operation,
        path,
        callback,
    ):
        try:
            callback()
        except Exception as error:
            failures.append(
                MountCleanupFailure(operation, path, error)
            )

    @staticmethod
    def _encode_json(value):
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _write_all(descriptor, raw):
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written == 0:
                raise OSError("Unable to write mount-session metadata")
            offset += written

    @staticmethod
    def _fsync_directory(path):
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
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

    @staticmethod
    def _is_hex(value, length):
        if not isinstance(value, str) or len(value) != length:
            return False
        return all(
            character in "0123456789abcdef"
            for character in value
        )
