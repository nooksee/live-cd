"""Caller-level ownership for mounts and host X access."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple

from . import mounts
from .. import messages


@dataclass(frozen=True)
class MountCleanupFailure:
    operation: str
    path: str
    error: BaseException


class MountAcquisitionError(messages.LiveUSBError):
    """Fail-closed error for incomplete mount or X acquisition."""

    def __init__(
        self,
        message: str,
        results: Iterable[mounts.MountAcquisition] = (),
    ):
        self.results = tuple(results)
        super().__init__(message)


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
    """Own mounts and X access acquired during one caller operation."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._entered = False
        self._preexisting_mounts: Set[str] = set()
        self._preserved_mounts: Set[str] = set()
        self._owned_mounts: List[mounts.MountAcquisition] = []
        self._active_mounts: Set[str] = set()
        self._created_directories: List[str] = []
        self._x_cleanup_required = False
        self._x_granted = False

    @property
    def owned_mounts(self) -> Tuple[str, ...]:
        return tuple(
            result.destination for result in self._owned_mounts
        )

    @property
    def preexisting_mounts(self) -> Tuple[str, ...]:
        return tuple(sorted(self._preexisting_mounts))

    def __enter__(self):
        if self._entered:
            raise MountAcquisitionError(
                "A mount session instance cannot be entered twice"
            )
        try:
            self._preexisting_mounts = set(
                mounts.mounted_paths(self.ctx)
            )
        except OSError as error:
            raise MountAcquisitionError(
                "Unable to inspect pre-existing FileSystem mounts"
            ) from error
        self._preserved_mounts = set(self._preexisting_mounts)
        self._entered = True
        return self

    def __exit__(self, _exc_type, primary_error, _traceback):
        try:
            self.cleanup()
        except MountSessionCleanupError as cleanup_error:
            if primary_error is not None:
                raise cleanup_error from primary_error
            raise
        return False

    def mount_sys(self):
        self._ensure_entered()
        return self._record_acquisitions(
            mounts.mount_sys(self.ctx)
        )

    def mount_dbus(self):
        self._ensure_entered()
        return self._record_acquisitions(
            mounts.mount_dbus(self.ctx)
        )

    def allow_local_x_access(self):
        self._ensure_entered()
        if self._x_granted:
            return
        self._x_cleanup_required = True
        try:
            succeeded = mounts.allow_local_x_access()
        except Exception as error:
            raise MountAcquisitionError(
                "Unable to grant local host X access"
            ) from error
        if not succeeded:
            raise MountAcquisitionError(
                "Unable to grant local host X access"
            )
        self._x_granted = True

    def cleanup(self):
        failures = []

        for result in reversed(self._owned_mounts):
            if result.destination not in self._active_mounts:
                continue
            try:
                unmount_result = mounts._umount_one(
                    result.destination,
                    result.label,
                )
            except Exception as error:
                failures.append(
                    MountCleanupFailure(
                        "unmount_owned",
                        result.destination,
                        error,
                    )
                )
                continue
            if unmount_result.outcome == mounts.UNMOUNT_FAILED:
                failures.append(
                    MountCleanupFailure(
                        "unmount_owned",
                        result.destination,
                        unmount_result.error
                        or messages.LiveUSBError(
                            "Owned mount cleanup failed"
                        ),
                    )
                )
            if not mounts.is_mounted(result.destination):
                self._active_mounts.discard(result.destination)

        try:
            backstop_results = mounts.recursive_umount(
                self.ctx,
                preserve=tuple(self._preserved_mounts),
            )
        except Exception as error:
            failures.append(
                MountCleanupFailure(
                    "recursive_unmount_backstop",
                    self.ctx.fs_dir,
                    error,
                )
            )
            backstop_results = tuple()

        for result in backstop_results:
            if result.outcome == mounts.UNMOUNT_FAILED:
                failures.append(
                    MountCleanupFailure(
                        "recursive_unmount_backstop",
                        result.destination,
                        result.error
                        or messages.LiveUSBError(
                            "Recursive unmount failed"
                        ),
                    )
                )

        for destination in tuple(self._active_mounts):
            if not mounts.is_mounted(destination):
                self._active_mounts.discard(destination)

        for directory in reversed(self._created_directories):
            if directory in self._active_mounts:
                continue
            try:
                os.rmdir(directory)
            except FileNotFoundError:
                self._created_directories.remove(directory)
            except OSError as error:
                failures.append(
                    MountCleanupFailure(
                        "remove_mount_directory",
                        directory,
                        error,
                    )
                )
            else:
                self._created_directories.remove(directory)

        if self._x_cleanup_required:
            try:
                revoked = mounts.block_local_x_access()
            except Exception as error:
                failures.append(
                    MountCleanupFailure(
                        "revoke_local_x_access",
                        "xhost -local:",
                        error,
                    )
                )
            else:
                if revoked:
                    self._x_cleanup_required = False
                    self._x_granted = False
                else:
                    failures.append(
                        MountCleanupFailure(
                            "revoke_local_x_access",
                            "xhost -local:",
                            messages.LiveUSBError(
                                "Unable to revoke local host X access"
                            ),
                        )
                    )

        if failures:
            raise MountSessionCleanupError(failures)

    def _record_acquisitions(self, results):
        results = tuple(results)
        for result in results:
            destination = os.path.abspath(result.destination)
            if (
                result.created_directory
                and destination not in self._created_directories
            ):
                self._created_directories.append(destination)
            if result.owned and destination not in self._active_mounts:
                self._owned_mounts.append(result)
                self._active_mounts.add(destination)
            elif (
                result.outcome == mounts.MOUNT_ALREADY_PRESENT
                and destination not in self._active_mounts
            ):
                self._preserved_mounts.add(destination)

        failed = tuple(
            result
            for result in results
            if result.outcome == mounts.MOUNT_FAILED
        )
        if failed:
            destinations = ", ".join(
                result.destination for result in failed
            )
            raise MountAcquisitionError(
                f"Mount acquisition failed: {destinations}",
                failed,
            )
        return results

    def _ensure_entered(self):
        if not self._entered:
            raise MountAcquisitionError(
                "Mount session has not been entered"
            )
