"""Crash-durable state management for one chroot command transaction."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import messages


HOSTS_SOURCE = "/etc/hosts"
RESOLV_CONF_SOURCE = "/etc/resolv.conf"
_LOCK_VERSION = 1
_JOURNAL_VERSION = 1
_JOURNAL_NAME = ".liveusb-chroot-transaction.json"
_JOURNAL_PENDING_MARKER = ".pending-"
_MAX_METADATA_BYTES = 1024 * 1024
_STUB_TARGET = "/bin/true"
_MANAGED_RELATIVE_PATHS = (
    "etc/hosts",
    "etc/resolv.conf",
    "etc/debian_chroot",
    "etc/mtab",
)
_SERVICE_STAGES = {
    "planned",
    "renamed",
    "stubbed",
    "preserve_replacement",
}


@dataclass(frozen=True)
class CleanupFailure:
    operation: str
    path: str
    error: BaseException


class TransactionCleanupError(messages.LiveUSBError):
    """One Python 3.8-compatible error containing ordered cleanup failures."""

    def __init__(self, failures: Iterable[CleanupFailure]):
        self.failures = tuple(failures)
        details = "; ".join(
            f"{failure.operation} [{failure.path}]: {failure.error}"
            for failure in self.failures
        )
        super().__init__(f"Chroot transaction cleanup failed: {details}")


class TransactionRecoveryError(messages.LiveUSBError):
    """Fail-closed error for unsafe or ambiguous recovery metadata."""


@dataclass
class _ManagedNode:
    path: str
    original_state: Dict[str, Any]
    backup_path: Optional[str]
    active: bool = True


@dataclass
class _BlockedFile:
    original_path: str
    backup_path: str
    original_state: Dict[str, Any]
    stage: str = "planned"
    stub_state: Optional[Dict[str, Any]] = None
    active: bool = True


class ChrootTransaction:
    """Own managed chroot nodes, service stubs, recovery, and one lock."""

    def __init__(self, ctx, host_sources: Optional[Dict[str, str]] = None):
        self.ctx = ctx
        self.fs_dir = os.path.abspath(ctx.fs_dir)
        self.work_dir = os.path.abspath(ctx.work_dir)
        self.lock_dir = os.path.join(self.fs_dir, "tmp")
        self.lock_path = os.path.join(self.lock_dir, "lock_chroot")
        self.journal_path = os.path.join(
            self.work_dir,
            _JOURNAL_NAME,
        )
        self.journal_pending_prefix = (
            self.journal_path + _JOURNAL_PENDING_MARKER
        )
        self.host_sources = (
            {
                "hosts": HOSTS_SOURCE,
                "resolv.conf": RESOLV_CONF_SOURCE,
            }
            if host_sources is None
            else dict(host_sources)
        )
        self._managed_nodes: List[_ManagedNode] = []
        self._blocked_files: List[_BlockedFile] = []
        self._deferred_cleanup_failures: List[CleanupFailure] = []
        self._external_cleanup_failures: List[CleanupFailure] = []
        self._blocked_cleanup_staged = False
        self._lock_owned = False
        self._lock_descriptor: Optional[int] = None
        self._lock_identity: Optional[Tuple[int, int]] = None
        self._owner_pid: Optional[int] = None
        self._token: Optional[str] = None
        self._lock_dir_created = False
        self._journal_active = False
        self._journal_sequence = 0

    def __del__(self):
        descriptor = getattr(self, "_lock_descriptor", None)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._lock_descriptor = None

    @property
    def blocked_files(self):
        return tuple(
            record.original_path
            for record in self._blocked_files
        )

    def __enter__(self):
        self._acquire_lock()
        try:
            self._initialize_journal()
            self._prepare_managed_nodes()
        except BaseException as primary_error:
            try:
                self.cleanup()
            except TransactionCleanupError as cleanup_error:
                raise cleanup_error from primary_error
            raise
        return self

    def __exit__(self, _exc_type, primary_error, _traceback):
        try:
            self.cleanup()
        except TransactionCleanupError as cleanup_error:
            if primary_error is not None:
                raise cleanup_error from primary_error
            raise
        return False

    def recover_stale(self):
        """Recover stale transaction evidence without starting new work."""

        if os.path.lexists(self.lock_path):
            self._recover_stale_transaction()
            self._reset_after_recovery()
            return True
        self._reject_orphan_metadata()
        return False

    def block_files(self, targets, stub_creator=None):
        creator = self._create_stub if stub_creator is None else stub_creator
        planned = []
        known_targets = {
            record.original_path
            for record in self._blocked_files
            if record.active
        }
        for target in targets:
            target = self._validated_absolute_path(target)
            self._reject_reserved_service_target(target)
            if target in known_targets:
                raise TransactionRecoveryError(
                    f"Duplicate service target is ambiguous: {target}"
                )
            known_targets.add(target)
            if not os.path.lexists(target):
                continue
            backup = target + ".blocked"
            if os.path.lexists(backup):
                raise TransactionRecoveryError(
                    f"Stale blocked-file backup is ambiguous: {backup}"
                )
            record = _BlockedFile(
                original_path=target,
                backup_path=backup,
                original_state=self._capture_node(target),
            )
            planned.append(record)

        if planned:
            self._blocked_files.extend(planned)
            self._persist_journal()

        for record in planned:
            self._rename_durable(
                record.original_path,
                record.backup_path,
            )
            record.stage = "renamed"
            self._persist_journal()

            result = creator(record.original_path)
            if getattr(result, "returncode", 0) != 0:
                raise messages.LiveUSBError(
                    "Unable to create service stub for "
                    f"{record.original_path}"
                )
            if not os.path.islink(record.original_path):
                raise messages.LiveUSBError(
                    "Service stub was not created for "
                    f"{record.original_path}"
                )
            if os.readlink(record.original_path) != _STUB_TARGET:
                raise messages.LiveUSBError(
                    "Service stub target is invalid for "
                    f"{record.original_path}"
                )

            self._fsync_directory(
                os.path.dirname(record.original_path)
            )
            record.stub_state = self._capture_node(
                record.original_path,
                include_change_time=True,
            )
            record.stage = "stubbed"
            self._persist_journal()

        return self.blocked_files

    def unblock_services(self):
        if self._blocked_cleanup_staged:
            return tuple()
        failures = self._attempt_blocked_file_cleanup()
        self._deferred_cleanup_failures.extend(failures)
        self._blocked_cleanup_staged = True
        return tuple(failures)

    def record_cleanup_failure(self, operation, path, error):
        self._external_cleanup_failures.append(
            CleanupFailure(operation, path, error)
        )

    def cleanup(self):
        failures = list(self._deferred_cleanup_failures)
        self._deferred_cleanup_failures.clear()

        if not self._blocked_cleanup_staged:
            failures.extend(self._attempt_blocked_file_cleanup())
        self._blocked_cleanup_staged = False

        failures.extend(self._external_cleanup_failures)
        self._external_cleanup_failures.clear()

        for state in reversed(self._managed_nodes):
            if not state.active:
                continue
            self._attempt_cleanup(
                failures,
                "restore_managed_node",
                state.path,
                lambda state=state: self._restore_managed_node(state),
            )

        active_records = any(
            state.active for state in self._managed_nodes
        ) or any(
            record.active for record in self._blocked_files
        )

        if self._journal_active and not active_records:
            self._attempt_cleanup(
                failures,
                "remove_transaction_journal",
                self.journal_path,
                self._remove_journal,
            )

        if self._lock_owned and not self._journal_active:
            self._attempt_cleanup(
                failures,
                "release_lock",
                self.lock_path,
                self._release_lock,
            )

        if self._lock_dir_created and not self._lock_owned:
            self._attempt_cleanup(
                failures,
                "remove_lock_directory",
                self.lock_dir,
                self._remove_created_lock_directory,
            )

        if failures:
            raise TransactionCleanupError(failures)

    def _acquire_lock(self):
        if os.path.lexists(self.lock_path):
            if not self.ctx.force_chroot:
                raise messages.LiveUSBError("FileSystem is locked!")
            self.recover_stale()
        else:
            self._reject_orphan_metadata()

        self._ensure_lock_directory()
        self._create_new_lock()

    def _recover_stale_transaction(self):
        descriptor = self._open_stale_lock()
        try:
            lock_data = self._read_lock_descriptor(descriptor)
            self._validate_lock_data(lock_data)
            # Exclusive flock acquisition is the ownership authority.
            # PID and token remain recovery identity and audit metadata;
            # PID liveness cannot distinguish reuse from the recorded owner.

            journal_bundle = self._read_recovery_bundle(lock_data)
            self._bind_stale_lock(descriptor, lock_data)
            descriptor = None
            try:
                self._preflight_recovery_state(
                    journal_bundle["managed"],
                    journal_bundle["services"],
                )
                self._validate_recovery_residue(
                    journal_bundle["managed"],
                    journal_bundle["services"],
                )
                self._validate_owned_lock_path()
                pending_path = journal_bundle["pending_path"]
                if pending_path is not None:
                    self._unlink_durable(pending_path)

                journal_data = journal_bundle["journal"]
                if journal_data is None:
                    self._release_lock()
                    return

                self._journal_active = True
                self._journal_sequence = journal_data["sequence"]
                self._managed_nodes = journal_bundle["managed"]
                self._blocked_files = journal_bundle["services"]
                self.cleanup()
            except BaseException:
                self._detach_stale_lock_handle()
                raise
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)

    def _preflight_recovery_state(self, managed, services):
        for state in managed:
            original_kind = state.original_state["kind"]
            backup_exists = (
                state.backup_path is not None
                and os.path.lexists(state.backup_path)
            )
            self._require_removable_node(state.path)
            if original_kind == "absent":
                if backup_exists:
                    raise TransactionRecoveryError(
                        "Unexpected backup for absent node: "
                        f"{state.path}"
                    )
                continue
            if backup_exists:
                if not self._node_matches(
                    state.backup_path,
                    state.original_state,
                ):
                    raise TransactionRecoveryError(
                        "Managed-node backup is ambiguous: "
                        f"{state.backup_path}"
                    )
                continue
            if not self._node_matches(
                state.path,
                state.original_state,
            ):
                raise TransactionRecoveryError(
                    "Managed-node recovery is incomplete: "
                    f"{state.path}"
                )

        for record in services:
            backup_exists = os.path.lexists(record.backup_path)
            original_exists = os.path.lexists(record.original_path)
            if backup_exists and not self._node_matches(
                record.backup_path,
                record.original_state,
            ):
                raise TransactionRecoveryError(
                    "Blocked-file backup is ambiguous: "
                    f"{record.backup_path}"
                )
            if record.stage in {"planned", "renamed"}:
                if backup_exists:
                    self._require_removable_node(
                        record.original_path
                    )
                if (
                    not backup_exists
                    and not self._node_matches(
                        record.original_path,
                        record.original_state,
                    )
                ):
                    raise TransactionRecoveryError(
                        "Pre-stub recovery is incomplete: "
                        f"{record.original_path}"
                    )
                continue

            owned_stub = self._is_owned_stub(record)
            if backup_exists:
                if (
                    record.stage == "preserve_replacement"
                    and (
                        not original_exists
                        or owned_stub
                    )
                ):
                    raise TransactionRecoveryError(
                        "Package replacement recovery is incomplete: "
                        f"{record.original_path}"
                    )
                if original_exists and not owned_stub:
                    self._require_regular_package_replacement(
                        record.original_path
                    )
                continue
            if self._node_matches(
                record.original_path,
                record.original_state,
            ):
                continue
            if (
                record.stage == "preserve_replacement"
                and original_exists
                and not owned_stub
            ):
                self._require_regular_package_replacement(
                    record.original_path
                )
                continue
            raise TransactionRecoveryError(
                "Blocked-file recovery is incomplete: "
                f"{record.original_path}"
            )

    def _validate_recovery_residue(self, managed, services):
        expected_paths = {
            state.backup_path
            for state in managed
            if state.backup_path is not None
        }
        expected_paths.update(
            record.backup_path for record in services
        )
        unexpected_paths = sorted(
            set(self._unjournaled_residue_paths()) - expected_paths
        )
        if unexpected_paths:
            raise TransactionRecoveryError(
                "Unjournaled transaction residue is ambiguous: "
                + ", ".join(unexpected_paths)
            )

    def _open_stale_lock(self):
        stat_result = os.lstat(self.lock_path)
        if not stat.S_ISREG(stat_result.st_mode):
            raise TransactionRecoveryError(
                "Chroot lock metadata is not a regular file"
            )
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags)
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            os.close(descriptor)
            raise TransactionRecoveryError(
                "Chroot transaction ownership is active"
            ) from error
        return descriptor

    def _bind_stale_lock(self, descriptor, lock_data):
        stat_result = os.fstat(descriptor)
        self._lock_descriptor = descriptor
        self._lock_identity = (
            stat_result.st_dev,
            stat_result.st_ino,
        )
        self._lock_owned = True
        self._owner_pid = lock_data["pid"]
        self._token = lock_data["token"]

    def _detach_stale_lock_handle(self):
        self._close_lock_descriptor()
        self._lock_owned = False
        self._lock_identity = None

    def _reset_after_recovery(self):
        self._managed_nodes = []
        self._blocked_files = []
        self._deferred_cleanup_failures = []
        self._external_cleanup_failures = []
        self._blocked_cleanup_staged = False
        self._lock_owned = False
        self._lock_identity = None
        self._owner_pid = None
        self._token = None
        self._journal_active = False
        self._journal_sequence = 0

    def _reject_orphan_metadata(self):
        pending_paths = self._pending_journal_paths()
        if os.path.lexists(self.journal_path) or pending_paths:
            raise TransactionRecoveryError(
                "Recovery metadata exists without a transaction lock"
            )
        if self._unjournaled_residue_paths():
            raise TransactionRecoveryError(
                "Transaction residue exists without recovery metadata"
            )

    def _reject_unjournaled_residue(self, token):
        residue = self._unjournaled_residue_paths(token)
        if residue:
            raise TransactionRecoveryError(
                "Unjournaled transaction residue is ambiguous"
            )

    def _unjournaled_residue_paths(self, token=None):
        suffix = (
            ".liveusb-transaction-"
            if token is None
            else f".liveusb-transaction-{token}"
        )
        residue = []
        for root, directory_names, file_names in os.walk(
            self.fs_dir,
            followlinks=False,
        ):
            names = tuple(directory_names) + tuple(file_names)
            for name in names:
                transaction_backup = (
                    suffix in name
                    if token is None
                    else name.endswith(suffix)
                )
                if transaction_backup or name.endswith(".blocked"):
                    residue.append(os.path.join(root, name))
        return tuple(residue)

    def _ensure_lock_directory(self):
        if not os.path.lexists(self.lock_dir):
            os.makedirs(self.lock_dir)
            self._fsync_directory(os.path.dirname(self.lock_dir))
            self._lock_dir_created = True
            return
        stat_result = os.lstat(self.lock_dir)
        if not stat.S_ISDIR(stat_result.st_mode):
            raise messages.LiveUSBError(
                f"Chroot lock directory is not usable: {self.lock_dir}"
            )

    def _create_new_lock(self):
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except FileExistsError as error:
            raise messages.LiveUSBError("FileSystem is locked!") from error

        self._lock_descriptor = descriptor
        self._lock_owned = True
        self._owner_pid = os.getpid()
        self._token = uuid.uuid4().hex
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            stat_result = os.fstat(descriptor)
            self._lock_identity = (
                stat_result.st_dev,
                stat_result.st_ino,
            )
            self._write_descriptor_json(
                descriptor,
                {
                    "pid": self._owner_pid,
                    "token": self._token,
                    "version": _LOCK_VERSION,
                },
            )
            self._fsync_directory(self.lock_dir)
        except BaseException as primary_error:
            failures = []
            self._attempt_cleanup(
                failures,
                "remove_incomplete_lock",
                self.lock_path,
                self._remove_incomplete_lock,
            )
            if failures:
                raise TransactionCleanupError(failures) from primary_error
            raise

    def _remove_incomplete_lock(self):
        try:
            if os.path.lexists(self.lock_path):
                os.unlink(self.lock_path)
                self._fsync_directory(self.lock_dir)
        finally:
            self._close_lock_descriptor()
            self._lock_owned = False
            self._lock_identity = None
            self._owner_pid = None
            self._token = None

    def _initialize_journal(self):
        if os.path.lexists(self.journal_path):
            raise TransactionRecoveryError(
                "A transaction journal already exists"
            )
        if self._pending_journal_paths():
            raise TransactionRecoveryError(
                "Pending transaction metadata already exists"
            )
        self._journal_sequence = 0
        self._journal_active = False
        self._persist_journal()

    def _prepare_managed_nodes(self):
        paths = tuple(
            os.path.join(self.fs_dir, relative_path)
            for relative_path in _MANAGED_RELATIVE_PATHS
        )
        planned = []
        for path in paths:
            original_state = self._capture_node(path)
            backup_path = (
                self._managed_backup_path(path)
                if original_state["kind"] != "absent"
                else None
            )
            if (
                backup_path is not None
                and os.path.lexists(backup_path)
            ):
                raise TransactionRecoveryError(
                    f"Managed-node backup already exists: {backup_path}"
                )
            planned.append(
                _ManagedNode(
                    path=path,
                    original_state=original_state,
                    backup_path=backup_path,
                )
            )

        self._managed_nodes.extend(planned)
        self._persist_journal()

        for state in planned:
            if state.backup_path is not None:
                self._rename_durable(
                    state.path,
                    state.backup_path,
                )

        hosts, resolv_conf, debian_chroot, mtab = paths
        self._copy_file_durable(
            self.host_sources["hosts"],
            hosts,
        )
        self._copy_file_durable(
            self.host_sources["resolv.conf"],
            resolv_conf,
        )
        self._write_file_durable(
            debian_chroot,
            b"chroot\n",
        )
        os.symlink("/proc/mounts", mtab)
        self._fsync_directory(os.path.dirname(mtab))

    def _attempt_blocked_file_cleanup(self):
        failures = []
        for record in self._blocked_files:
            if not record.active:
                continue
            self._attempt_cleanup(
                failures,
                "restore_blocked_file",
                record.original_path,
                lambda record=record: self._restore_blocked_file(record),
            )
        return failures

    def _restore_managed_node(self, state):
        self._recover_managed_node(state)
        state.active = False
        try:
            self._persist_journal()
        except BaseException:
            state.active = True
            raise

    def _recover_managed_node(self, state):
        original_kind = state.original_state["kind"]
        backup_exists = (
            state.backup_path is not None
            and os.path.lexists(state.backup_path)
        )

        if original_kind == "absent":
            if backup_exists:
                raise TransactionRecoveryError(
                    f"Unexpected backup for absent node: {state.path}"
                )
            self._remove_node_durable(state.path)
            return

        if backup_exists:
            if not self._node_matches(
                state.backup_path,
                state.original_state,
            ):
                raise TransactionRecoveryError(
                    f"Managed-node backup is ambiguous: {state.backup_path}"
                )
            self._remove_node_durable(state.path)
            self._rename_durable(
                state.backup_path,
                state.path,
            )
            return

        if self._node_matches(state.path, state.original_state):
            return

        raise TransactionRecoveryError(
            f"Managed-node recovery is incomplete: {state.path}"
        )

    def _restore_blocked_file(self, record):
        self._recover_blocked_file(record)
        record.active = False
        try:
            self._persist_journal()
        except BaseException:
            record.active = True
            raise

    def _recover_blocked_file(self, record):
        backup_exists = os.path.lexists(record.backup_path)
        original_exists = os.path.lexists(record.original_path)

        if backup_exists and not self._node_matches(
            record.backup_path,
            record.original_state,
        ):
            raise TransactionRecoveryError(
                f"Blocked-file backup is ambiguous: {record.backup_path}"
            )

        if record.stage in {"planned", "renamed"}:
            if not backup_exists:
                if self._node_matches(
                    record.original_path,
                    record.original_state,
                ):
                    return
                raise TransactionRecoveryError(
                    "Pre-stub recovery is incomplete: "
                    f"{record.original_path}"
                )
            self._remove_node_durable(record.original_path)
            self._rename_durable(
                record.backup_path,
                record.original_path,
            )
            return

        owned_stub = self._is_owned_stub(record)
        if backup_exists:
            if record.stage == "preserve_replacement":
                if not original_exists or owned_stub:
                    raise TransactionRecoveryError(
                        "Package replacement recovery is incomplete: "
                        f"{record.original_path}"
                    )
                self._require_regular_package_replacement(
                    record.original_path
                )
                messages.warning(
                    f"{record.original_path} has been updated, "
                    "removing blocked file!"
                )
                self._unlink_durable(record.backup_path)
                return

            if not original_exists or owned_stub:
                self._remove_node_durable(record.original_path)
                self._rename_durable(
                    record.backup_path,
                    record.original_path,
                )
                return

            self._require_regular_package_replacement(
                record.original_path
            )
            previous_stage = record.stage
            previous_sequence = self._journal_sequence
            record.stage = "preserve_replacement"
            try:
                self._persist_journal()
            except BaseException:
                if self._journal_sequence == previous_sequence:
                    record.stage = previous_stage
                raise
            messages.warning(
                f"{record.original_path} has been updated, "
                "removing blocked file!"
            )
            self._unlink_durable(record.backup_path)
            return

        if self._node_matches(
            record.original_path,
            record.original_state,
        ):
            return
        if (
            record.stage == "preserve_replacement"
            and original_exists
            and not owned_stub
        ):
            self._require_regular_package_replacement(
                record.original_path
            )
            return

        raise TransactionRecoveryError(
            f"Blocked-file recovery is incomplete: {record.original_path}"
        )

    @staticmethod
    def _require_regular_package_replacement(path):
        try:
            stat_result = os.lstat(path)
        except FileNotFoundError as error:
            raise TransactionRecoveryError(
                f"Package replacement is absent: {path}"
            ) from error
        if not stat.S_ISREG(stat_result.st_mode):
            raise TransactionRecoveryError(
                "Package replacement is not an lstat regular file: "
                f"{path}"
            )

    def _is_owned_stub(self, record):
        if not os.path.islink(record.original_path):
            return False
        try:
            target = os.readlink(record.original_path)
        except OSError:
            return False
        if target != _STUB_TARGET:
            return False
        if record.stub_state is None:
            return record.stage in {"planned", "renamed"}
        return self._node_matches(
            record.original_path,
            record.stub_state,
        )

    def _persist_journal(self):
        if not self._lock_owned or self._token is None:
            raise TransactionRecoveryError(
                "A journal cannot be written without owned locking"
            )
        pending_paths = self._pending_journal_paths()
        if pending_paths:
            raise TransactionRecoveryError(
                "Pending journal metadata is ambiguous"
            )
        if self._journal_active:
            current = self._read_journal_path(
                self.journal_path,
                self._owner_pid,
                self._token,
            )
            if current["sequence"] != self._journal_sequence:
                raise TransactionRecoveryError(
                    "Transaction journal sequence changed"
                )
        elif os.path.lexists(self.journal_path):
            raise TransactionRecoveryError(
                "Unexpected transaction journal exists"
            )

        next_sequence = self._journal_sequence + 1
        data = self._journal_data(next_sequence)
        try:
            self._write_json_atomically(self.journal_path, data)
        except BaseException:
            if os.path.lexists(self.journal_path):
                try:
                    current = self._read_journal_path(
                        self.journal_path,
                        self._owner_pid,
                        self._token,
                    )
                except (OSError, TransactionRecoveryError):
                    current = None
                if current == data:
                    self._journal_sequence = next_sequence
                    self._journal_active = True
            raise
        self._journal_sequence = next_sequence
        self._journal_active = True

    def _journal_data(self, sequence):
        return {
            "managed": [
                self._managed_record_data(state)
                for state in self._managed_nodes
                if state.active
            ],
            "owner": {
                "pid": self._owner_pid,
                "token": self._token,
            },
            "sequence": sequence,
            "services": [
                self._service_record_data(record)
                for record in self._blocked_files
                if record.active
            ],
            "version": _JOURNAL_VERSION,
        }

    def _managed_record_data(self, state):
        return {
            "backup": (
                None
                if state.backup_path is None
                else self._relative_path(state.backup_path)
            ),
            "kind": "managed",
            "original": state.original_state,
            "path": self._relative_path(state.path),
        }

    def _service_record_data(self, record):
        return {
            "backup": self._relative_path(record.backup_path),
            "kind": "service",
            "original": record.original_state,
            "path": self._relative_path(record.original_path),
            "stage": record.stage,
            "stub": record.stub_state,
        }

    def _remove_journal(self):
        if self._pending_journal_paths():
            raise TransactionRecoveryError(
                "Pending journal metadata prevents finalization"
            )
        if not os.path.lexists(self.journal_path):
            self._journal_active = False
            self._journal_sequence = 0
            return
        current = self._read_journal_path(
            self.journal_path,
            self._owner_pid,
            self._token,
        )
        if current["sequence"] != self._journal_sequence:
            raise TransactionRecoveryError(
                "Transaction journal sequence changed"
            )
        if current["managed"] or current["services"]:
            raise TransactionRecoveryError(
                "Transaction journal still contains active records"
            )
        self._unlink_durable(self.journal_path)
        self._journal_active = False
        self._journal_sequence = 0

    def _release_lock(self):
        self._validate_owned_lock_path()
        os.unlink(self.lock_path)
        self._fsync_directory(self.lock_dir)
        self._close_lock_descriptor()
        self._clear_lock_state()

    def _validate_owned_lock_path(self):
        if not os.path.lexists(self.lock_path):
            raise TransactionRecoveryError(
                f"Chroot lock ownership changed: {self.lock_path}"
            )
        stat_result = os.lstat(self.lock_path)
        current_identity = (
            stat_result.st_dev,
            stat_result.st_ino,
        )
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or current_identity != self._lock_identity
        ):
            raise TransactionRecoveryError(
                f"Chroot lock ownership changed: {self.lock_path}"
            )

        lock_data = self._read_lock_descriptor(
            self._lock_descriptor
        )
        self._validate_lock_data(lock_data)
        if (
            lock_data["pid"] != self._owner_pid
            or lock_data["token"] != self._token
        ):
            raise TransactionRecoveryError(
                f"Chroot lock token changed: {self.lock_path}"
            )

    def _clear_lock_state(self):
        self._lock_owned = False
        self._lock_identity = None
        self._owner_pid = None
        self._token = None

    def _close_lock_descriptor(self):
        if self._lock_descriptor is None:
            return
        try:
            fcntl.flock(
                self._lock_descriptor,
                fcntl.LOCK_UN,
            )
        finally:
            os.close(self._lock_descriptor)
            self._lock_descriptor = None

    def _remove_created_lock_directory(self):
        if not os.path.lexists(self.lock_dir):
            self._lock_dir_created = False
            return
        os.rmdir(self.lock_dir)
        self._fsync_directory(os.path.dirname(self.lock_dir))
        self._lock_dir_created = False

    def _read_recovery_bundle(self, lock_data):
        pending_paths = self._pending_journal_paths()
        if len(pending_paths) > 1:
            raise TransactionRecoveryError(
                "Multiple pending transaction journals are ambiguous"
            )

        journal_data = None
        managed = []
        services = []
        if os.path.lexists(self.journal_path):
            journal_data, managed, services = self._decode_journal(
                self.journal_path,
                lock_data["pid"],
                lock_data["token"],
            )

        pending_path = (
            pending_paths[0] if pending_paths else None
        )
        if pending_path is not None:
            pending_data, _pending_managed, _pending_services = (
                self._decode_journal(
                    pending_path,
                    lock_data["pid"],
                    lock_data["token"],
                )
            )
            if journal_data is None:
                if (
                    pending_data["sequence"] != 1
                    or pending_data["managed"]
                    or pending_data["services"]
                ):
                    raise TransactionRecoveryError(
                        "Pending initial journal is ambiguous"
                    )
            elif (
                pending_data["sequence"]
                != journal_data["sequence"] + 1
            ):
                raise TransactionRecoveryError(
                    "Pending journal sequence is ambiguous"
                )

        return {
            "journal": journal_data,
            "managed": managed,
            "pending_path": pending_path,
            "services": services,
        }

    def _read_journal_path(self, path, owner_pid, token):
        data, _managed, _services = self._decode_journal(
            path,
            owner_pid,
            token,
        )
        return data

    def _decode_journal(self, path, owner_pid, token):
        data = self._read_json_path(path)
        required_keys = {
            "managed",
            "owner",
            "sequence",
            "services",
            "version",
        }
        if not isinstance(data, dict) or set(data) != required_keys:
            raise TransactionRecoveryError(
                f"Transaction journal schema is invalid: {path}"
            )
        if data["version"] != _JOURNAL_VERSION:
            raise TransactionRecoveryError(
                f"Transaction journal version is invalid: {path}"
            )
        owner = data["owner"]
        if (
            not isinstance(owner, dict)
            or set(owner) != {"pid", "token"}
            or owner["pid"] != owner_pid
            or owner["token"] != token
        ):
            raise TransactionRecoveryError(
                f"Transaction journal ownership is invalid: {path}"
            )
        if (
            type(data["sequence"]) is not int
            or data["sequence"] < 1
        ):
            raise TransactionRecoveryError(
                f"Transaction journal sequence is invalid: {path}"
            )
        if (
            not isinstance(data["managed"], list)
            or not isinstance(data["services"], list)
        ):
            raise TransactionRecoveryError(
                f"Transaction journal records are invalid: {path}"
            )

        managed = [
            self._decode_managed_record(record, token)
            for record in data["managed"]
        ]
        services = [
            self._decode_service_record(record)
            for record in data["services"]
        ]
        relative_paths = [
            self._relative_path(state.path)
            for state in managed
        ] + [
            self._relative_path(record.original_path)
            for record in services
        ]
        if len(relative_paths) != len(set(relative_paths)):
            raise TransactionRecoveryError(
                f"Transaction journal paths are ambiguous: {path}"
            )
        return data, managed, services

    def _decode_managed_record(self, record, token):
        required_keys = {
            "backup",
            "kind",
            "original",
            "path",
        }
        if (
            not isinstance(record, dict)
            or set(record) != required_keys
            or record["kind"] != "managed"
            or record["path"] not in _MANAGED_RELATIVE_PATHS
        ):
            raise TransactionRecoveryError(
                "Managed-node journal record is invalid"
            )
        path = self._absolute_relative_path(record["path"])
        original_state = self._validate_node_state(
            record["original"],
            allow_change_time=False,
        )
        expected_backup = (
            None
            if original_state["kind"] == "absent"
            else self._managed_backup_path(path, token)
        )
        backup_value = record["backup"]
        backup_path = (
            None
            if backup_value is None
            else self._absolute_relative_path(backup_value)
        )
        if backup_path != expected_backup:
            raise TransactionRecoveryError(
                "Managed-node backup metadata is invalid"
            )
        return _ManagedNode(
            path=path,
            original_state=original_state,
            backup_path=backup_path,
        )

    def _decode_service_record(self, record):
        required_keys = {
            "backup",
            "kind",
            "original",
            "path",
            "stage",
            "stub",
        }
        if (
            not isinstance(record, dict)
            or set(record) != required_keys
            or record["kind"] != "service"
            or record["stage"] not in _SERVICE_STAGES
        ):
            raise TransactionRecoveryError(
                "Service journal record is invalid"
            )
        path = self._absolute_relative_path(record["path"])
        self._reject_reserved_service_target(path)
        backup_path = self._absolute_relative_path(record["backup"])
        if backup_path != path + ".blocked":
            raise TransactionRecoveryError(
                "Service backup metadata is invalid"
            )
        original_state = self._validate_node_state(
            record["original"],
            allow_change_time=False,
        )
        if original_state["kind"] == "absent":
            raise TransactionRecoveryError(
                "Service original state cannot be absent"
            )
        stub_state = (
            None
            if record["stub"] is None
            else self._validate_node_state(
                record["stub"],
                allow_change_time=True,
            )
        )
        if (
            record["stage"] in {"stubbed", "preserve_replacement"}
            and stub_state is None
        ):
            raise TransactionRecoveryError(
                "Completed service stub metadata is missing"
            )
        if (
            record["stage"] in {"planned", "renamed"}
            and stub_state is not None
        ):
            raise TransactionRecoveryError(
                "Pre-stub service metadata is ambiguous"
            )
        return _BlockedFile(
            original_path=path,
            backup_path=backup_path,
            original_state=original_state,
            stage=record["stage"],
            stub_state=stub_state,
        )

    def _validate_node_state(self, value, allow_change_time):
        if not isinstance(value, dict) or "kind" not in value:
            raise TransactionRecoveryError(
                "Node-state metadata is invalid"
            )
        kind = value["kind"]
        if kind == "absent":
            if set(value) != {"kind"}:
                raise TransactionRecoveryError(
                    "Absent-node metadata is invalid"
                )
            return dict(value)
        if kind == "file":
            required = {
                "dev",
                "ino",
                "kind",
                "mode",
                "sha256",
                "size",
            }
        elif kind == "symlink":
            required = {
                "dev",
                "ino",
                "kind",
                "mode",
                "target",
            }
        else:
            raise TransactionRecoveryError(
                f"Unsupported node-state kind: {kind}"
            )
        if allow_change_time:
            required = set(required)
            required.add("ctime_ns")
        if set(value) != required:
            raise TransactionRecoveryError(
                "Node-state fields are invalid"
            )
        for key in ("dev", "ino", "mode"):
            if type(value[key]) is not int or value[key] < 0:
                raise TransactionRecoveryError(
                    "Node-state numeric metadata is invalid"
                )
        if allow_change_time and (
            type(value["ctime_ns"]) is not int
            or value["ctime_ns"] < 0
        ):
            raise TransactionRecoveryError(
                "Node change-time metadata is invalid"
            )
        if kind == "file":
            if (
                type(value["size"]) is not int
                or value["size"] < 0
                or not self._is_hex(value["sha256"], 64)
            ):
                raise TransactionRecoveryError(
                    "File-state metadata is invalid"
                )
        elif not isinstance(value["target"], str):
            raise TransactionRecoveryError(
                "Symlink-state metadata is invalid"
            )
        return dict(value)

    def _read_lock_descriptor(self, descriptor):
        stat_result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or stat_result.st_size > _MAX_METADATA_BYTES
        ):
            raise TransactionRecoveryError(
                "Chroot lock metadata is invalid"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, _MAX_METADATA_BYTES + 1)
        if len(raw) > _MAX_METADATA_BYTES:
            raise TransactionRecoveryError(
                "Chroot lock metadata is too large"
            )
        return self._decode_json(raw, self.lock_path)

    def _validate_lock_data(self, data):
        if (
            not isinstance(data, dict)
            or set(data) != {"pid", "token", "version"}
            or data["version"] != _LOCK_VERSION
            or type(data["pid"]) is not int
            or data["pid"] < 1
            or not self._is_hex(data["token"], 32)
        ):
            raise TransactionRecoveryError(
                "Chroot lock metadata is invalid"
            )

    def _read_json_path(self, path):
        stat_result = os.lstat(path)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or stat_result.st_size > _MAX_METADATA_BYTES
        ):
            raise TransactionRecoveryError(
                f"Recovery metadata is invalid: {path}"
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            raw = os.read(descriptor, _MAX_METADATA_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_METADATA_BYTES:
            raise TransactionRecoveryError(
                f"Recovery metadata is too large: {path}"
            )
        return self._decode_json(raw, path)

    @staticmethod
    def _decode_json(raw, path):
        try:
            text = raw.decode("utf-8")
            return json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransactionRecoveryError(
                f"Recovery metadata is corrupt: {path}"
            ) from error

    def _write_descriptor_json(self, descriptor, data):
        raw = self._encode_json(data)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        self._write_all(descriptor, raw)
        os.fsync(descriptor)

    def _write_json_atomically(self, path, data):
        pending_path = (
            self.journal_pending_prefix + uuid.uuid4().hex
        )
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(pending_path, flags, 0o600)
            self._write_all(descriptor, self._encode_json(data))
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(pending_path, path)
            self._fsync_directory(os.path.dirname(path))
        except BaseException as primary_error:
            if descriptor is not None:
                os.close(descriptor)
            failures = []
            if os.path.lexists(pending_path):
                self._attempt_cleanup(
                    failures,
                    "remove_pending_journal",
                    pending_path,
                    lambda: self._unlink_durable(pending_path),
                )
            if failures:
                raise TransactionCleanupError(failures) from primary_error
            raise

    @staticmethod
    def _encode_json(data):
        return (
            json.dumps(
                data,
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
                raise OSError("Unable to write transaction metadata")
            offset += written

    def _capture_node(self, path, include_change_time=False):
        if not os.path.lexists(path):
            return {"kind": "absent"}
        stat_result = os.lstat(path)
        common = {
            "dev": stat_result.st_dev,
            "ino": stat_result.st_ino,
            "mode": stat.S_IMODE(stat_result.st_mode),
        }
        if stat.S_ISREG(stat_result.st_mode):
            state = {
                **common,
                "kind": "file",
                "sha256": self._hash_file(path),
                "size": stat_result.st_size,
            }
        elif stat.S_ISLNK(stat_result.st_mode):
            state = {
                **common,
                "kind": "symlink",
                "target": os.readlink(path),
            }
        else:
            raise TransactionRecoveryError(
                f"Unsupported transaction node: {path}"
            )
        if include_change_time:
            state["ctime_ns"] = stat_result.st_ctime_ns
        return state

    def _node_matches(self, path, expected):
        if expected["kind"] == "absent":
            return not os.path.lexists(path)
        if not os.path.lexists(path):
            return False
        try:
            actual = self._capture_node(
                path,
                include_change_time="ctime_ns" in expected,
            )
        except (OSError, TransactionRecoveryError):
            return False
        return actual == expected

    @staticmethod
    def _hash_file(path):
        digest = hashlib.sha256()
        with open(path, "rb") as file_handle:
            while True:
                chunk = file_handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _managed_backup_path(self, path, token=None):
        selected_token = self._token if token is None else token
        if selected_token is None:
            raise TransactionRecoveryError(
                "Managed-node backup token is missing"
            )
        return f"{path}.liveusb-transaction-{selected_token}"

    def _relative_path(self, path):
        absolute = self._validated_absolute_path(path)
        relative = os.path.relpath(absolute, self.fs_dir)
        if relative == "." or relative.startswith(".." + os.sep):
            raise TransactionRecoveryError(
                f"Transaction path escapes FileSystem: {path}"
            )
        return relative

    def _absolute_relative_path(self, relative):
        if (
            not isinstance(relative, str)
            or not relative
            or os.path.isabs(relative)
            or os.path.normpath(relative) != relative
            or relative == ".."
            or relative.startswith(".." + os.sep)
        ):
            raise TransactionRecoveryError(
                "Recovery path metadata is invalid"
            )
        return self._validated_absolute_path(
            os.path.join(self.fs_dir, relative)
        )

    def _validated_absolute_path(self, path):
        absolute = os.path.abspath(path)
        try:
            common = os.path.commonpath((self.fs_dir, absolute))
        except ValueError as error:
            raise TransactionRecoveryError(
                f"Transaction path is invalid: {path}"
            ) from error
        if common != self.fs_dir or absolute == self.fs_dir:
            raise TransactionRecoveryError(
                f"Transaction path escapes FileSystem: {path}"
            )
        parent_real = os.path.realpath(os.path.dirname(absolute))
        fs_real = os.path.realpath(self.fs_dir)
        try:
            real_common = os.path.commonpath((fs_real, parent_real))
        except ValueError as error:
            raise TransactionRecoveryError(
                f"Transaction parent is invalid: {path}"
            ) from error
        if real_common != fs_real:
            raise TransactionRecoveryError(
                f"Transaction parent escapes FileSystem: {path}"
            )
        return absolute

    def _reject_reserved_service_target(self, path):
        managed_paths = {
            os.path.join(self.fs_dir, relative_path)
            for relative_path in _MANAGED_RELATIVE_PATHS
        }
        if path in managed_paths or path == self.lock_path:
            raise TransactionRecoveryError(
                f"Transaction-reserved service target: {path}"
            )
        relative_parts = os.path.relpath(
            path,
            self.fs_dir,
        ).split(os.sep)
        if any(
            part.endswith(".blocked")
            or ".liveusb-transaction-" in part
            for part in relative_parts
        ):
            raise TransactionRecoveryError(
                f"Transaction-reserved service target: {path}"
            )

    def _pending_journal_paths(self):
        parent = os.path.dirname(self.journal_path)
        if not os.path.isdir(parent):
            return tuple()
        prefix = os.path.basename(self.journal_pending_prefix)
        return tuple(
            sorted(
                os.path.join(parent, name)
                for name in os.listdir(parent)
                if name.startswith(prefix)
            )
        )

    def _copy_file_durable(self, source, destination):
        shutil.copyfile(source, destination)
        with open(destination, "rb") as file_handle:
            os.fsync(file_handle.fileno())
        self._fsync_directory(os.path.dirname(destination))

    def _write_file_durable(self, path, content):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o666)
        try:
            self._write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._fsync_directory(os.path.dirname(path))

    def _rename_durable(self, source, destination):
        os.rename(source, destination)
        source_parent = os.path.dirname(source)
        destination_parent = os.path.dirname(destination)
        self._fsync_directory(source_parent)
        if destination_parent != source_parent:
            self._fsync_directory(destination_parent)

    def _unlink_durable(self, path):
        os.unlink(path)
        self._fsync_directory(os.path.dirname(path))

    def _remove_node_durable(self, path):
        if not os.path.lexists(path):
            return
        self._require_removable_node(path)
        os.unlink(path)
        self._fsync_directory(os.path.dirname(path))

    @staticmethod
    def _require_removable_node(path):
        if not os.path.lexists(path):
            return
        stat_result = os.lstat(path)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            and not stat.S_ISLNK(stat_result.st_mode)
        ):
            raise TransactionRecoveryError(
                f"Transaction node type is ambiguous: {path}"
            )

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
    def _pid_is_live(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _is_hex(value, length):
        if not isinstance(value, str) or len(value) != length:
            return False
        return all(character in "0123456789abcdef" for character in value)

    @staticmethod
    def _create_stub(path):
        os.symlink(_STUB_TARGET, path)

    @staticmethod
    def _attempt_cleanup(failures, operation, path, callback):
        try:
            callback()
        except Exception as error:
            failures.append(
                CleanupFailure(operation, path, error)
            )
