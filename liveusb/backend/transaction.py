"""Failure-safe state management for one chroot command transaction."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .. import messages


HOSTS_SOURCE = "/etc/hosts"
RESOLV_CONF_SOURCE = "/etc/resolv.conf"
_STUB_TARGET = "/bin/true"


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


@dataclass
class _ManagedNode:
    path: str
    existed: bool
    backup_path: Optional[str] = None
    active: bool = False


@dataclass
class _BlockedFile:
    original_path: str
    backup_path: str
    stub_identity: Optional[Tuple[int, int, int]] = None
    stub_complete: bool = False
    active: bool = True


class ChrootTransaction:
    """Own managed chroot nodes, service stubs, and one atomic lock."""

    def __init__(self, ctx, host_sources: Optional[Dict[str, str]] = None):
        self.ctx = ctx
        self.fs_dir = ctx.fs_dir
        self.lock_dir = os.path.join(self.fs_dir, "tmp")
        self.lock_path = os.path.join(self.lock_dir, "lock_chroot")
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
        self._lock_identity: Optional[Tuple[int, int]] = None
        self._lock_token: Optional[bytes] = None
        self._lock_token_complete = False
        self._lock_dir_created = False

    @property
    def blocked_files(self):
        return tuple(record.original_path for record in self._blocked_files)

    def __enter__(self):
        try:
            self._acquire_lock()
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

    def block_files(self, targets, stub_creator=None):
        creator = self._create_stub if stub_creator is None else stub_creator
        for target in targets:
            if not os.path.lexists(target):
                continue
            backup = target + ".blocked"
            if os.path.lexists(backup):
                messages.warning(f"Blocking of {target} skipped!")
                continue

            os.rename(target, backup)
            record = _BlockedFile(
                original_path=target,
                backup_path=backup,
            )
            self._blocked_files.append(record)

            result = creator(target)
            if getattr(result, "returncode", 0) != 0:
                raise messages.LiveUSBError(
                    f"Unable to create service stub for {target}"
                )
            if not os.path.islink(target):
                raise messages.LiveUSBError(
                    f"Service stub was not created for {target}"
                )
            if os.readlink(target) != _STUB_TARGET:
                raise messages.LiveUSBError(
                    f"Service stub target is invalid for {target}"
                )

            stat_result = os.lstat(target)
            record.stub_identity = (
                stat_result.st_dev,
                stat_result.st_ino,
                stat_result.st_ctime_ns,
            )
            record.stub_complete = True

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

        if self._lock_owned:
            self._attempt_cleanup(
                failures,
                "release_lock",
                self.lock_path,
                self._release_lock,
            )

        if self._lock_dir_created:
            self._attempt_cleanup(
                failures,
                "remove_lock_directory",
                self.lock_dir,
                self._remove_created_lock_directory,
            )

        if failures:
            raise TransactionCleanupError(failures)

    def _acquire_lock(self):
        if not os.path.lexists(self.lock_dir):
            os.makedirs(self.lock_dir)
            self._lock_dir_created = True
        elif not os.path.isdir(self.lock_dir):
            raise messages.LiveUSBError(
                f"Chroot lock directory is not usable: {self.lock_dir}"
            )

        if os.path.lexists(self.lock_path):
            if not self.ctx.force_chroot:
                raise messages.LiveUSBError("FileSystem is locked!")
            os.unlink(self.lock_path)

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        self._lock_token = uuid.uuid4().hex.encode("ascii")
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except FileExistsError as error:
            raise messages.LiveUSBError("FileSystem is locked!") from error

        self._lock_owned = True
        try:
            stat_result = os.fstat(descriptor)
            self._lock_identity = (
                stat_result.st_dev,
                stat_result.st_ino,
            )
            offset = 0
            while offset < len(self._lock_token):
                written = os.write(
                    descriptor,
                    self._lock_token[offset:],
                )
                if written == 0:
                    raise OSError("Unable to write chroot lock token")
                offset += written
            os.fsync(descriptor)
            self._lock_token_complete = True
        finally:
            os.close(descriptor)

    def _prepare_managed_nodes(self):
        etc_dir = os.path.join(self.fs_dir, "etc")
        managed_paths = (
            os.path.join(etc_dir, "hosts"),
            os.path.join(etc_dir, "resolv.conf"),
            os.path.join(etc_dir, "debian_chroot"),
            os.path.join(etc_dir, "mtab"),
        )
        for path in managed_paths:
            self._preserve_managed_node(path)

        shutil.copyfile(
            self.host_sources["hosts"],
            managed_paths[0],
        )
        shutil.copyfile(
            self.host_sources["resolv.conf"],
            managed_paths[1],
        )
        with open(managed_paths[2], "w", encoding="utf-8") as file_handle:
            file_handle.write("chroot\n")
        os.symlink("/proc/mounts", managed_paths[3])

    def _preserve_managed_node(self, path):
        existed = os.path.lexists(path)
        state = _ManagedNode(path=path, existed=existed)
        self._managed_nodes.append(state)
        if existed:
            state.backup_path = self._new_backup_path(path)
            os.rename(path, state.backup_path)
        state.active = True

    def _new_backup_path(self, path):
        while True:
            candidate = (
                f"{path}.liveusb-transaction-{uuid.uuid4().hex}"
            )
            if not os.path.lexists(candidate):
                return candidate

    def _restore_managed_node(self, state):
        self._remove_node(state.path)
        if state.existed:
            if state.backup_path is None:
                raise OSError(f"Managed-node backup is missing: {state.path}")
            os.rename(state.backup_path, state.path)
        state.active = False

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

    def _restore_blocked_file(self, record):
        if not record.stub_complete:
            self._remove_node(record.original_path)
            os.rename(record.backup_path, record.original_path)
            record.active = False
            return

        if not os.path.lexists(record.original_path):
            os.rename(record.backup_path, record.original_path)
            record.active = False
            return

        current_stat = os.lstat(record.original_path)
        current_identity = (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_ctime_ns,
        )
        owned_stub = (
            stat.S_ISLNK(current_stat.st_mode)
            and current_identity == record.stub_identity
            and os.readlink(record.original_path) == _STUB_TARGET
        )
        if owned_stub:
            self._remove_node(record.original_path)
            os.rename(record.backup_path, record.original_path)
        else:
            messages.warning(
                f"{record.original_path} has been updated, "
                "removing blocked file!"
            )
            os.unlink(record.backup_path)
        record.active = False

    def _release_lock(self):
        if not os.path.lexists(self.lock_path):
            self._lock_owned = False
            self._lock_identity = None
            self._lock_token = None
            self._lock_token_complete = False
            return

        stat_result = os.lstat(self.lock_path)
        current_identity = (
            stat_result.st_dev,
            stat_result.st_ino,
        )
        if current_identity != self._lock_identity:
            raise OSError(
                f"Chroot lock ownership changed: {self.lock_path}"
            )
        if self._lock_token_complete:
            with open(self.lock_path, "rb") as file_handle:
                current_token = file_handle.read()
            if current_token != self._lock_token:
                raise OSError(
                    f"Chroot lock token changed: {self.lock_path}"
                )
        os.unlink(self.lock_path)
        self._lock_owned = False
        self._lock_identity = None
        self._lock_token = None
        self._lock_token_complete = False

    def _remove_created_lock_directory(self):
        if not os.path.lexists(self.lock_dir):
            self._lock_dir_created = False
            return
        os.rmdir(self.lock_dir)
        self._lock_dir_created = False

    @staticmethod
    def _create_stub(path):
        os.symlink(_STUB_TARGET, path)

    @staticmethod
    def _remove_node(path):
        if not os.path.lexists(path):
            return
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)

    @staticmethod
    def _attempt_cleanup(failures, operation, path, callback):
        try:
            callback()
        except Exception as error:
            failures.append(
                CleanupFailure(operation, path, error)
            )
