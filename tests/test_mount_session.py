from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import Context
from liveusb.backend import mount_session
from liveusb.backend import mounts
from liveusb.backend.mount_session import (
    MountAcquisitionError,
    MountRecoveryError,
    MountSession,
    MountSessionCleanupError,
)


def mount_identity(
    mount_id,
    path,
    parent_id=1,
    source="/synthetic",
    major_minor="0:1",
    root="/",
    fs_type="none",
    mount_options=("rw",),
    optional_fields=(),
    super_options=("rw",),
):
    return mounts.MountIdentity(
        mount_id=mount_id,
        parent_id=parent_id,
        major_minor=major_minor,
        root=root,
        mount_point=os.path.abspath(path),
        mount_options=tuple(mount_options),
        optional_fields=tuple(optional_fields),
        fs_type=fs_type,
        source=source,
        super_options=tuple(super_options),
    )


class FakeMountTable:
    def __init__(self):
        self.identities = []
        self.next_id = 100
        self.mount_commands = []
        self.unmount_commands = []
        self.fail_next_mount = False
        self.ambiguous_next_mount = False
        self.nested_on_rbind = False
        self.fail_unmount_paths = set()

    def reader(self):
        return tuple(self.identities)

    def add(
        self,
        path,
        parent_id=1,
        source="/synthetic",
        major_minor="0:1",
        root="/",
        fs_type="none",
        mount_options=("rw",),
        optional_fields=(),
        super_options=("rw",),
    ):
        identity = mount_identity(
            self.next_id,
            path,
            parent_id=parent_id,
            source=source,
            major_minor=major_minor,
            root=root,
            fs_type=fs_type,
            mount_options=mount_options,
            optional_fields=optional_fields,
            super_options=super_options,
        )
        self.next_id += 1
        self.identities.append(identity)
        return identity

    def remove_path(self, path):
        absolute = os.path.abspath(path)
        self.identities = [
            identity
            for identity in self.identities
            if identity.mount_point != absolute
        ]

    def replace_path(self, path):
        self.remove_path(path)
        return self.add(path, source="/replacement")

    def mount(self, command):
        command = list(command)
        self.mount_commands.append(command)
        destination = os.path.abspath(command[-1])
        source = command[-2]
        if self.fail_next_mount:
            self.fail_next_mount = False
            self.add(destination, source="/external")
            return False
        if self.ambiguous_next_mount:
            self.ambiguous_next_mount = False
            self.add(destination, source=source)
            self.add(destination, source="/concurrent")
            return True
        root = self.add(destination, source=source)
        if "--rbind" in command and self.nested_on_rbind:
            self.add(
                os.path.join(destination, "pts"),
                parent_id=root.mount_id,
                source="/dev/pts",
            )
        return True

    def unmount(self, command):
        command = list(command)
        self.unmount_commands.append(command)
        destination = os.path.abspath(command[-1])
        if destination in self.fail_unmount_paths:
            return False
        candidates = [
            identity
            for identity in self.identities
            if identity.mount_point == destination
        ]
        if len(candidates) != 1:
            return False
        self.identities.remove(candidates[0])
        return True


class FakeXAccess:
    def __init__(self, enabled=True, local_present=False):
        self.state = mounts.XAccessState(
            enabled=enabled,
            local_present=local_present,
        )
        self.mutations = []
        self.fail_grant = False
        self.fail_restore = False
        self.query_error = None

    def query(self):
        if self.query_error is not None:
            raise self.query_error
        return self.state

    def mutate(self, add):
        self.mutations.append(add)
        if add and self.fail_grant:
            return False
        if not add and self.fail_restore:
            return False
        self.state = mounts.XAccessState(
            enabled=self.state.enabled,
            local_present=add,
        )
        return True


class MountEvidenceTests(unittest.TestCase):
    def test_exact_mountpoint_does_not_match_prefix_collision(self):
        text = (
            "10 1 0:1 / /work/FileSystem/dev rw - none /dev rw\n"
            "11 1 0:2 / /work/FileSystem/device rw - none /device rw\n"
        )

        identities = mounts.parse_mountinfo(text)

        self.assertEqual(
            tuple(
                identity.mount_id
                for identity in mounts.mounts_at(
                    identities,
                    "/work/FileSystem/dev",
                )
            ),
            (10,),
        )

    def test_mountinfo_decodes_escaped_space_exactly(self):
        identities = mounts.parse_mountinfo(
            "20 1 0:3 / /work/FileSystem/name\\040with\\040space "
            "rw - none /source\\040name rw\n"
        )

        self.assertEqual(
            identities[0].mount_point,
            "/work/FileSystem/name with space",
        )
        self.assertEqual(identities[0].source, "/source name")

    def test_mountinfo_decodes_tab_newline_and_backslash(self):
        identities = mounts.parse_mountinfo(
            "21 1 0:3 / "
            "/work/FileSystem/tab\\011line\\012slash\\134name "
            "rw - none /source\\011value rw\n"
        )

        self.assertEqual(
            identities[0].mount_point,
            "/work/FileSystem/tab\tline\nslash\\name",
        )
        self.assertEqual(
            identities[0].source,
            "/source\tvalue",
        )

    def test_stacked_mounts_remain_distinct_identities(self):
        identities = mounts.parse_mountinfo(
            "30 1 0:4 / /work/FileSystem/dev rw - none /one rw\n"
            "31 1 0:5 / /work/FileSystem/dev rw - none /two rw\n"
        )

        self.assertEqual(
            tuple(
                identity.mount_id
                for identity in mounts.mounts_at(
                    identities,
                    "/work/FileSystem/dev",
                )
            ),
            (30, 31),
        )

    def test_xhost_parser_rejects_unknown_output(self):
        with self.assertRaises(mounts.XAccessEvidenceError):
            mounts.parse_xhost_output("some unknown response\n")

    def test_xhost_parser_preserves_enabled_local_and_other_entries(self):
        state = mounts.parse_xhost_output(
            "access control enabled, only authorized clients can connect\n"
            "SI:localuser:operator\n"
            "LOCAL:\n"
        )
        self.assertEqual(
            state,
            mounts.XAccessState(
                enabled=True,
                local_present=True,
            ),
        )

    def test_xhost_parser_accepts_realistic_ipv6_entries(self):
        state = mounts.parse_xhost_output(
            "access control enabled, only authorized clients can connect\n"
            "INET6:::1\n"
            "INET6:fe80::1\n"
            "INET:127.0.0.1\n"
            "SI:localuser:operator\n"
        )

        self.assertTrue(state.enabled)
        self.assertFalse(state.local_present)

    def test_xhost_parser_recognizes_disabled_access_control(self):
        state = mounts.parse_xhost_output(
            "access control disabled, clients can connect from any host\n"
        )

        self.assertEqual(
            state,
            mounts.XAccessState(
                enabled=False,
                local_present=False,
            ),
        )

    def test_xhost_parser_rejects_status_variants_and_unknown_entries(self):
        payloads = (
            "prefix access control enabled, only authorized clients "
            "can connect\n",
            "access control enabled, only authorized clients can "
            "connect suffix\n",
            "access control enabled, only authorized clients can connect\n"
            "UNKNOWN:entry\n",
            "access control enabled, only authorized clients can connect\n"
            "INET6:\n",
            "access control disabled, clients can connect from any host\n"
            "access control enabled, only authorized clients can connect\n",
            " access control enabled, only authorized clients can connect\n",
            "access control enabled, only authorized clients can connect \n",
            "access control enabled, only authorized clients can connect\n\n"
            "LOCAL:\n",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(
                    mounts.XAccessEvidenceError
                ):
                    mounts.parse_xhost_output(payload)

    def test_xhost_runner_forces_deterministic_c_locale(self):
        completed = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(
            mounts.subprocess,
            "run",
            return_value=completed,
        ) as run_command:
            mounts._default_x_runner(["xhost"])

        environment = run_command.call_args.kwargs["env"]
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual(environment["LC_ALL"], "C")

    def test_xhost_parser_accepts_named_hosts_and_zones(self):
        state = mounts.parse_xhost_output(
            "access control enabled, only authorized clients can connect\n"
            "INET6:localhost\n"
            "INET6:ip6-localhost\n"
            "INET6:fe80::1%eth0\n"
            "INET6:not-an-address\n"
        )

        self.assertTrue(state.enabled)
        self.assertFalse(state.local_present)

    def test_nosymfollow_mismatch_rejects_mount_equivalence(self):
        source = mount_identity(
            40,
            "/source",
            mount_options=("rw", "nosymfollow"),
        )
        destination = mount_identity(
            41,
            "/destination",
            source=source.source,
            major_minor=source.major_minor,
            root=source.root,
            fs_type=source.fs_type,
            mount_options=("rw",),
        )
        request = mounts.MountRequest(
            "/source",
            "/destination",
            "test",
            ("--bind",),
        )

        with self.assertRaisesRegex(
            mounts.MountEvidenceError,
            "does not match",
        ):
            mounts.prove_preexisting_mount(
                request,
                (source, destination),
            )


class MountSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.work_dir = self.root / "work"
        self.fs_dir = self.work_dir / "FileSystem"
        self.runtime_dir = self.root / "runtime"
        for relative in ("etc", "usr", "root", "tmp"):
            (self.fs_dir / relative).mkdir(
                parents=True,
                exist_ok=True,
            )
        self.ctx = Context(
            work_dir=str(self.work_dir),
            mount_dir=str(self.root / "mount"),
            runtime_dir=str(self.runtime_dir),
        )
        self.table = FakeMountTable()
        self.x_access = FakeXAccess(
            enabled=False,
            local_present=False,
        )

    def session(self):
        return MountSession(
            self.ctx,
            mountinfo_reader=self.table.reader,
            mount_runner=self.table.mount,
            unmount_runner=self.table.unmount,
            x_query=self.x_access.query,
            x_mutator=self.x_access.mutate,
        )

    def assert_runtime_clean(self):
        self.assertFalse(
            os.path.lexists(
                self.runtime_dir / "mount-session.json"
            )
        )
        pending = tuple(
            self.runtime_dir.glob("mount-session.json.pending-*")
        )
        self.assertEqual(pending, ())

    def test_preexisting_mount_is_not_owned_or_unmounted(self):
        source = self.table.add(
            "/dev",
            source="/dev-device",
        )
        existing = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
        )

        with self.session() as session:
            acquisitions = session.mount_sys()

        self.assertEqual(
            acquisitions[0].outcome,
            mounts.MOUNT_ALREADY_PRESENT,
        )
        self.assertEqual(acquisitions[0].owned, ())
        self.assertIn(existing, self.table.identities)
        self.assertIn(source, self.table.identities)
        self.assertNotIn(
            str(self.fs_dir / "dev"),
            tuple(command[-1] for command in self.table.unmount_commands),
        )
        self.assert_runtime_clean()

    def test_wrong_preexisting_mount_fails_before_mount_command(self):
        self.table.add("/dev", source="/dev-device")
        wrong = self.table.add(
            self.fs_dir / "dev",
            source="/wrong-device",
        )

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_sys()

        self.assertEqual(self.table.mount_commands, [])
        self.assertIn(wrong, self.table.identities)
        self.assert_runtime_clean()

    def test_preexisting_mount_option_mismatch_is_rejected(self):
        mismatches = (
            (("rw",), ("ro",)),
            (("rw",), ("rw", "nosuid")),
            (("rw",), ("rw", "nodev")),
            (("rw",), ("rw", "noexec")),
            (("rw", "relatime"), ("rw", "noatime")),
            (("rw",), ("rw",), ("shared:10",), ()),
        )
        for case in mismatches:
            with self.subTest(case=case):
                self.table = FakeMountTable()
                source_options, destination_options = case[:2]
                source_propagation = (
                    case[2] if len(case) > 2 else ()
                )
                destination_propagation = (
                    case[3] if len(case) > 3 else ()
                )
                self.table.add(
                    "/dev",
                    source="/dev-device",
                    mount_options=source_options,
                    super_options=source_options,
                    optional_fields=source_propagation,
                )
                existing = self.table.add(
                    self.fs_dir / "dev",
                    source="/dev-device",
                    mount_options=destination_options,
                    super_options=destination_options,
                    optional_fields=destination_propagation,
                )

                with self.assertRaises(MountAcquisitionError):
                    with self.session() as session:
                        session.mount_sys()

                self.assertEqual(self.table.mount_commands, [])
                self.assertIn(existing, self.table.identities)
                self.assert_runtime_clean()

    def test_preexisting_mount_ignores_propagation_numeric_identity(self):
        source = self.table.add(
            "/dev",
            source="/dev-device",
            optional_fields=("shared:10",),
        )
        existing = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
            optional_fields=("shared:99",),
        )

        with self.session() as session:
            acquisitions = session.mount_sys()

        self.assertEqual(
            acquisitions[0].outcome,
            mounts.MOUNT_ALREADY_PRESENT,
        )
        self.assertIn(source, self.table.identities)
        self.assertIn(existing, self.table.identities)
        self.assert_runtime_clean()

    def test_mount_access_is_not_masked_by_superblock_access(self):
        self.table.add(
            "/dev",
            source="/dev-device",
            mount_options=("rw",),
            super_options=("ro",),
        )
        existing = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
            mount_options=("ro",),
            super_options=("ro",),
        )

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_sys()

        self.assertEqual(self.table.mount_commands, [])
        self.assertIn(existing, self.table.identities)
        self.assert_runtime_clean()

    def test_unproved_preexisting_mount_fails_before_mount_command(self):
        unproved = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
        )

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_sys()

        self.assertEqual(self.table.mount_commands, [])
        self.assertIn(unproved, self.table.identities)
        self.assert_runtime_clean()

    def test_stacked_preexisting_mount_fails_before_mount_command(self):
        self.table.add("/dev", source="/dev-device")
        first = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
        )
        second = self.table.add(
            self.fs_dir / "dev",
            source="/dev-device",
        )

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_sys()

        self.assertEqual(self.table.mount_commands, [])
        self.assertIn(first, self.table.identities)
        self.assertIn(second, self.table.identities)
        self.assert_runtime_clean()

    def test_owned_mounts_and_nested_rbind_cleanup_in_reverse_order(self):
        self.table.nested_on_rbind = True

        with self.session() as session:
            acquisitions = session.mount_sys()
            unrelated = self.table.add(
                self.fs_dir / "unrelated",
                source="/unrelated",
            )

        self.assertTrue(
            all(
                acquisition.outcome == mounts.MOUNT_CREATED
                for acquisition in acquisitions
            )
        )
        self.assertEqual(
            tuple(
                command[-1]
                for command in self.table.unmount_commands
            ),
            (
                str(self.fs_dir / "sys"),
                str(self.fs_dir / "proc"),
                str(self.fs_dir / "dev/pts"),
                str(self.fs_dir / "dev"),
            ),
        )
        self.assertEqual(self.table.identities, [unrelated])
        self.assert_runtime_clean()

    def test_later_nested_mount_with_owned_ancestry_is_cleaned(self):
        with self.session() as session:
            acquisitions = session.mount_sys()
            owned_dev = acquisitions[0].owned[0]
            self.table.add(
                self.fs_dir / "dev/shm",
                parent_id=owned_dev.mount_id,
                source="/dev/shm",
            )

        self.assertEqual(
            tuple(
                command[-1]
                for command in self.table.unmount_commands
            ),
            (
                str(self.fs_dir / "sys"),
                str(self.fs_dir / "proc"),
                str(self.fs_dir / "dev/shm"),
                str(self.fs_dir / "dev"),
            ),
        )
        self.assertEqual(self.table.identities, [])
        self.assert_runtime_clean()

    def test_later_nested_mount_without_owned_ancestry_is_preserved(self):
        session = self.session()
        session.__enter__()
        session.mount_sys()
        ambiguous = self.table.add(
            self.fs_dir / "dev/unrelated",
            parent_id=1,
            source="/unrelated",
        )

        with self.assertRaises(MountSessionCleanupError):
            session.__exit__(None, None, None)

        self.assertEqual(self.table.unmount_commands, [])
        self.assertIn(ambiguous, self.table.identities)
        self.assertTrue(os.path.lexists(session.journal_path))

    def test_failed_mount_owns_zero_even_if_another_mount_appears(self):
        self.table.fail_next_mount = True
        session = self.session()

        with self.assertRaises(MountSessionCleanupError) as captured:
            with session:
                session.mount_sys()

        self.assertIsInstance(
            captured.exception.__cause__,
            MountAcquisitionError,
        )
        self.assertEqual(self.table.unmount_commands, [])
        external_path = self.fs_dir / "dev"
        self.assertEqual(
            len(mounts.mounts_at(self.table.reader(), external_path)),
            1,
        )
        self.assertTrue(os.path.lexists(session.journal_path))

        self.table.remove_path(external_path)
        session.cleanup()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertFalse(external_path.exists())
        self.assert_runtime_clean()

    def test_failed_mount_with_preexisting_directory_keeps_journal(self):
        destination = self.fs_dir / "dev"
        destination.mkdir()
        self.table.fail_next_mount = True
        session = self.session()

        with self.assertRaises(MountSessionCleanupError):
            with session:
                session.mount_sys()

        self.assertEqual(self.table.unmount_commands, [])
        self.assertTrue(destination.is_dir())
        self.assertTrue(os.path.lexists(session.journal_path))

        self.table.remove_path(destination)
        session.cleanup()

        self.assertTrue(destination.is_dir())
        self.assert_runtime_clean()

    def test_ambiguous_concurrent_acquisition_is_preserved(self):
        self.table.ambiguous_next_mount = True
        session = self.session()

        with self.assertRaises(MountSessionCleanupError) as captured:
            with session:
                session.mount_sys()

        self.assertIsInstance(
            captured.exception.__cause__,
            MountAcquisitionError,
        )
        self.assertEqual(self.table.unmount_commands, [])
        self.assertEqual(
            len(
                mounts.mounts_at(
                    self.table.reader(),
                    self.fs_dir / "dev",
                )
            ),
            2,
        )
        self.assertTrue(os.path.lexists(session.journal_path))
        self.table.remove_path(self.fs_dir / "dev")
        session.cleanup()
        self.assertEqual(self.table.unmount_commands, [])
        self.assert_runtime_clean()

    def test_replacement_mount_fails_before_any_cleanup_mutation(self):
        session = self.session()
        session.__enter__()
        session.mount_sys()
        self.table.replace_path(self.fs_dir / "sys")

        with self.assertRaises(MountSessionCleanupError):
            session.__exit__(None, None, None)

        self.assertEqual(self.table.unmount_commands, [])
        self.assertTrue(os.path.lexists(session.journal_path))

    def test_symlinked_final_target_executes_mount_zero_times(self):
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(self.fs_dir / "dev"))

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_sys()

        self.assertEqual(self.table.mount_commands, [])
        self.assert_runtime_clean()

    def test_symlinked_parent_escape_executes_mount_zero_times(self):
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(self.fs_dir / "var"))

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.mount_dbus()

        self.assertEqual(self.table.mount_commands, [])
        self.assert_runtime_clean()

    def test_legitimate_var_run_link_stays_inside_filesystem(self):
        (self.fs_dir / "var").mkdir()
        (self.fs_dir / "run").mkdir()
        os.symlink("../run", str(self.fs_dir / "var/run"))

        with self.session() as session:
            acquisitions = session.mount_dbus()

        self.assertEqual(acquisitions[1].source, "/run/dbus")
        self.assertEqual(
            acquisitions[1].destination,
            str(self.fs_dir / "run/dbus"),
        )
        self.assertTrue(os.path.islink(self.fs_dir / "var/run"))
        self.assert_runtime_clean()

    def test_competing_live_operation_lock_is_rejected_by_flock(self):
        first = self.session()
        second = self.session()
        first.__enter__()
        try:
            with self.assertRaisesRegex(
                MountRecoveryError,
                "holds the runtime lock",
            ):
                second.__enter__()
        finally:
            first.__exit__(None, None, None)

        self.assertTrue(
            os.path.lexists(self.runtime_dir / "operation.lock")
        )
        with second:
            pass
        self.assert_runtime_clean()

    def test_x_disabled_performs_no_acl_mutation(self):
        self.x_access = FakeXAccess(
            enabled=False,
            local_present=False,
        )

        with self.session() as session:
            session.allow_local_x_access()

        self.assertEqual(self.x_access.mutations, [])
        self.assert_runtime_clean()

    def test_no_change_x_state_may_be_reevaluated_for_a_grant(self):
        self.x_access = FakeXAccess(
            enabled=False,
            local_present=False,
        )

        with self.session() as session:
            session.allow_local_x_access()
            self.x_access.state = mounts.XAccessState(
                enabled=True,
                local_present=False,
            )
            session.allow_local_x_access()

        self.assertEqual(self.x_access.mutations, [True, False])
        self.assertFalse(self.x_access.state.local_present)
        self.assert_runtime_clean()

    def test_preexisting_local_acl_is_not_revoked(self):
        self.x_access = FakeXAccess(
            enabled=True,
            local_present=True,
        )

        with self.session() as session:
            session.allow_local_x_access()

        self.assertEqual(self.x_access.mutations, [])
        self.assertTrue(self.x_access.state.local_present)
        self.assert_runtime_clean()

    def test_session_owned_local_acl_is_granted_and_revoked(self):
        self.x_access = FakeXAccess(
            enabled=True,
            local_present=False,
        )

        with self.session() as session:
            session.allow_local_x_access()
            self.assertTrue(self.x_access.state.local_present)

        self.assertEqual(self.x_access.mutations, [True, False])
        self.assertEqual(
            self.x_access.state,
            mounts.XAccessState(
                enabled=True,
                local_present=False,
            ),
        )
        self.assert_runtime_clean()

    def test_x_grant_failure_is_nonblocking_and_leaves_no_mutation(self):
        self.x_access = FakeXAccess(
            enabled=True,
            local_present=False,
        )
        self.x_access.fail_grant = True

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.allow_local_x_access()

        self.assertEqual(self.x_access.mutations, [True])
        self.assertFalse(self.x_access.state.local_present)
        self.assert_runtime_clean()

    def test_x_restore_failure_preserves_journal_for_retry(self):
        self.x_access = FakeXAccess(
            enabled=True,
            local_present=False,
        )
        self.x_access.fail_restore = True
        session = self.session()

        with self.assertRaises(MountSessionCleanupError):
            with session:
                session.allow_local_x_access()

        self.assertTrue(self.x_access.state.local_present)
        self.assertTrue(os.path.lexists(session.journal_path))

        self.x_access.fail_restore = False
        session.cleanup()

        self.assertFalse(self.x_access.state.local_present)
        self.assert_runtime_clean()

    def test_unparsable_x_state_fails_before_mutation(self):
        self.x_access.query_error = mounts.XAccessEvidenceError(
            "synthetic unparsable output"
        )

        with self.assertRaises(MountAcquisitionError):
            with self.session() as session:
                session.allow_local_x_access()

        self.assertEqual(self.x_access.mutations, [])
        self.assert_runtime_clean()

    def test_every_created_directory_is_removed_in_reverse_order(self):
        shutil.rmtree(self.fs_dir / "tmp")
        removed = []
        real_rmdir = os.rmdir

        def recording_rmdir(path):
            removed.append(os.path.abspath(path))
            return real_rmdir(path)

        with mock.patch.object(
            mount_session.os,
            "rmdir",
            side_effect=recording_rmdir,
        ):
            with self.session() as session:
                session.mount_dbus()

        expected = (
            str(self.fs_dir / "var/run/dbus"),
            str(self.fs_dir / "var/run"),
            str(self.fs_dir / "var/lib/dbus"),
            str(self.fs_dir / "var/lib"),
            str(self.fs_dir / "var"),
        )
        self.assertEqual(tuple(removed), expected)
        self.assert_runtime_clean()

    def test_generic_directory_final_mode_is_umask_independent(self):
        shutil.rmtree(self.fs_dir / "tmp")
        previous_umask = os.umask(0o077)
        try:
            session = self.session()
            session.__enter__()
            session._ensure_directory(str(self.fs_dir / "tmp"))
            self.assertEqual(
                os.stat(self.fs_dir / "tmp").st_mode & 0o777,
                0o755,
            )
            session.cleanup()
            session._release_runtime_lock()
        finally:
            os.umask(previous_umask)

        self.assertFalse((self.fs_dir / "tmp").exists())
        self.assert_runtime_clean()

    def test_mounted_created_directory_identity_is_deferred(self):
        destination = self.fs_dir / "dev"
        real_matcher = mounts.directory_identity_matches

        with self.session() as session:
            session.mount_sys()

            def mounted_view(path, expected):
                if (
                    os.path.abspath(path) == str(destination)
                    and mounts.mounts_at(
                        self.table.reader(),
                        destination,
                    )
                ):
                    return False
                return real_matcher(path, expected)

            with mock.patch.object(
                mounts,
                "directory_identity_matches",
                side_effect=mounted_view,
            ):
                session.cleanup()

        self.assertFalse(destination.exists())
        self.assert_runtime_clean()

    def test_staging_uses_unique_paths_and_preserves_legacy_names(self):
        legacy_deb = self.fs_dir / "tmp/temp.deb"
        legacy_hook = self.fs_dir / "tmp/HOOK"
        legacy_deb.write_bytes(b"legacy deb")
        legacy_hook.write_bytes(b"legacy hook")
        source_deb = self.root / "source.deb"
        source_hook = self.root / "source-hook"
        source_deb.write_bytes(b"new deb")
        source_hook.write_bytes(b"new hook")

        with self.session() as session:
            deb_path = session.stage_file(
                str(source_deb),
                "deb",
                suffix=".deb",
            )
            hook_path = session.stage_file(
                str(source_hook),
                "hook",
                executable=True,
            )
            self.assertNotEqual(deb_path, "/tmp/temp.deb")
            self.assertNotEqual(hook_path, "/tmp/HOOK")

        self.assertEqual(legacy_deb.read_bytes(), b"legacy deb")
        self.assertEqual(legacy_hook.read_bytes(), b"legacy hook")
        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        self.assert_runtime_clean()

    def test_staging_cleans_after_ordinary_exception(self):
        source = self.root / "source.deb"
        source.write_bytes(b"payload")

        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with self.session() as session:
                session.stage_file(
                    str(source),
                    "deb",
                    suffix=".deb",
                )
                raise RuntimeError("synthetic failure")

        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        self.assert_runtime_clean()

    def test_writing_artifact_uses_recorded_inode_for_cleanup(self):
        payload = b"partial-copy-payload"
        source = self.root / "source.deb"
        source.write_bytes(payload)
        session = self.session()
        real_write_all = session._write_all

        def fail_payload_write(descriptor, raw):
            if raw == payload:
                os.write(descriptor, raw[:7])
                raise OSError("synthetic staging write failure")
            return real_write_all(descriptor, raw)

        with self.assertRaisesRegex(
            OSError,
            "synthetic staging write failure",
        ):
            with session:
                with mock.patch.object(
                    session,
                    "_write_all",
                    side_effect=fail_payload_write,
                ):
                    session.stage_file(
                        str(source),
                        "deb",
                        suffix=".deb",
                    )

        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        self.assert_runtime_clean()

    def test_active_artifact_disappearance_fails_closed(self):
        source = self.root / "source.deb"
        source.write_bytes(b"active artifact")
        session = self.session()
        session.__enter__()
        session.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        artifact = tuple(
            (self.fs_dir / "tmp").glob("liveusb-*")
        )[0]
        os.unlink(artifact)

        with self.assertRaises(MountSessionCleanupError):
            session.__exit__(None, None, None)

        self.assertTrue(os.path.lexists(session.journal_path))

    def test_staging_cleans_when_later_mount_acquisition_fails(self):
        source = self.root / "source.deb"
        source.write_bytes(b"payload")
        self.table.fail_next_mount = True
        session = self.session()

        with self.assertRaises(MountSessionCleanupError):
            with session:
                session.stage_file(
                    str(source),
                    "deb",
                    suffix=".deb",
                )
                session.mount_sys()

        self.assertEqual(
            len(tuple((self.fs_dir / "tmp").glob("liveusb-*"))),
            1,
        )
        self.assertTrue(os.path.lexists(session.journal_path))
        self.table.remove_path(self.fs_dir / "dev")
        session.cleanup()
        self.assertEqual(
            tuple((self.fs_dir / "tmp").glob("liveusb-*")),
            (),
        )
        self.assert_runtime_clean()

    def test_same_object_cleanup_retry_removes_staged_artifact(self):
        source = self.root / "source.deb"
        source.write_bytes(b"payload")
        session = self.session()
        session.__enter__()
        staged = session.stage_file(
            str(source),
            "deb",
            suffix=".deb",
        )
        staged_host = self.fs_dir / staged.lstrip("/")
        real_unlink = os.unlink
        failed = {"value": False}

        def fail_once(path):
            if os.path.abspath(path) == str(staged_host) and not failed[
                "value"
            ]:
                failed["value"] = True
                raise OSError("synthetic unlink failure")
            return real_unlink(path)

        with mock.patch.object(
            mount_session.os,
            "unlink",
            side_effect=fail_once,
        ):
            with self.assertRaises(MountSessionCleanupError):
                session.cleanup()

        session.cleanup()
        session.__exit__(None, None, None)

        self.assertFalse(staged_host.exists())
        self.assert_runtime_clean()


if __name__ == "__main__":
    unittest.main()
