from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from liveusb.backend import rebuild


class RebuildHelperCharacterizationTests(unittest.TestCase):
    def test_efi_detection_matches_positive_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "boot.cfg").write_bytes(
                b"linux /casper/vmlinuz.efi quiet splash\n"
            )

            self.assertTrue(
                rebuild._media_references_vmlinuz_efi(str(root))
            )

    def test_efi_detection_rejects_negative_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "boot.cfg").write_bytes(
                b"linux /casper/vmlinuz quiet splash\n"
            )
            (root / "notes.txt").write_bytes(b"ordinary media content\n")

            self.assertFalse(
                rebuild._media_references_vmlinuz_efi(str(root))
            )

    def test_efi_detection_rejects_a_misleading_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vmlinuz.efi").write_bytes(b"ordinary kernel payload\n")

            self.assertFalse(
                rebuild._media_references_vmlinuz_efi(str(root))
            )

    def test_efi_detection_matches_nested_media_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "boot/grub/grub.cfg"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"linuxefi /casper/vmlinuz.efi\n")

            self.assertTrue(
                rebuild._media_references_vmlinuz_efi(str(root))
            )

    def test_efi_detection_continues_after_a_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unreadable = root / "unreadable"
            positive = root / "positive"
            unreadable.write_bytes(b"ordinary content\n")
            positive.write_bytes(b"linux /casper/vmlinuz.efi\n")
            real_open = open

            def guarded_open(path, *args, **kwargs):
                if path == str(unreadable):
                    raise OSError("simulated read failure")
                return real_open(path, *args, **kwargs)

            with mock.patch.object(
                rebuild,
                "_walk_files",
                return_value=iter((str(unreadable), str(positive))),
            ), mock.patch(
                "builtins.open",
                side_effect=guarded_open,
            ):
                result = rebuild._media_references_vmlinuz_efi(str(root))

            self.assertTrue(result)

    def test_efi_detection_matches_across_a_chunk_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix_size = rebuild._MEDIA_SCAN_CHUNK_SIZE - 4
            (root / "boot.cfg").write_bytes(
                b"x" * prefix_size + b"vmlinuz.efi\n"
            )

            self.assertTrue(
                rebuild._media_references_vmlinuz_efi(str(root))
            )

    def test_md5_generation_is_sorted_deterministic_and_excludes_manifests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payloads = {
                "alpha/first.txt": b"first\n",
                "alpha/second.txt": b"second\n",
                "z-last.bin": b"\x00\x01\x02",
            }
            for relative_path, content in payloads.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            (root / "alpha/md5sum.txt").write_text(
                "nested manifest must be excluded\n",
                encoding="utf-8",
            )
            output_path = root / "md5sum.txt"

            rebuild._write_md5sums(str(root), str(output_path))
            first_output = output_path.read_text(encoding="utf-8")
            rebuild._write_md5sums(str(root), str(output_path))
            second_output = output_path.read_text(encoding="utf-8")

            expected_lines = [
                f"{hashlib.md5(payloads[path]).hexdigest()}  ./{path}"
                for path in sorted(payloads)
            ]
            self.assertEqual(first_output.splitlines(), expected_lines)
            self.assertEqual(second_output, first_output)
            self.assertNotIn("md5sum.txt", first_output)


if __name__ == "__main__":
    unittest.main()
