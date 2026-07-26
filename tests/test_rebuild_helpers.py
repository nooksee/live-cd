from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from liveusb.backend import rebuild


class RebuildHelperCharacterizationTests(unittest.TestCase):
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
