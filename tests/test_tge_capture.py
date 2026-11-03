import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.capture_tge_validation import write_manifest


class CaptureOutputTest(unittest.TestCase):
    def test_existing_capture_is_preserved(self):
        script = Path(__file__).resolve().parents[1] / "tools/capture_tge_validation.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            original = {
                "manifest.json": b'{"entries":[]}',
                "tge_delivery_2026-11-02.html": b"<html>original</html>",
                "tge_delivery_2026-11-02.csv": b"original reference\n",
            }
            for name, content in original.items():
                (output / name).write_bytes(content)

            result = subprocess.run(
                [sys.executable, str(script), "--start", "2026-11-02",
                 "--days", "1", "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Output path already exists", result.stderr)
            self.assertEqual(
                {path.name: path.read_bytes() for path in output.iterdir()}, original
            )

    def test_manifest_is_published_as_complete_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = {"entries": [{"hours": 24}], "errors": []}
            write_manifest(output, manifest)

            path = output / "manifest.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), manifest)
            self.assertEqual(list(output.iterdir()), [path])

    def test_failed_serialization_leaves_no_manifest_or_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with self.assertRaises(TypeError):
                write_manifest(output, {"entries": [object()]})
            self.assertEqual(list(output.iterdir()), [])
