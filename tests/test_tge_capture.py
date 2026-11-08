import csv
from datetime import date
from hashlib import sha256
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shadow_ingest_manifest import ingest_manifest
from tools.capture_tge_validation import capture, extract_reference, write_manifest


class CaptureOutputTest(unittest.TestCase):
    def test_autumn_capture_keeps_repeated_labels_separate_and_cannot_be_imported(self):
        fixture = Path(__file__).parent / 'fixtures/tge_rdn_session_2026-10-24_captured_2026-11-02.html'
        with tempfile.TemporaryDirectory() as directory, patch('tools.capture_tge_validation.urlopen') as fetch:
            output = Path(directory)
            response = fetch.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = fixture.read_bytes()

            with self.assertRaisesRegex(ValueError, 'requires a 25-hour Warsaw day'):
                capture(date(2026, 10, 26), output, autumn_25h=True)
            fetch.assert_not_called()
            entry = capture(date(2026, 10, 25), output, autumn_25h=True)
            reference = (output / entry['reference_file']).read_bytes()
            rows = list(csv.DictReader(reference.decode().splitlines()))
            self.assertEqual((entry['period_basis'], entry['hours'], len(rows)), ('TGE_LABEL_ONLY', 25, 25))
            self.assertEqual(entry['reference_sha256'], sha256(reference).hexdigest())
            self.assertEqual(
                [(row['tge_hour_label'], row['price_pln_mwh']) for row in rows[1:3]],
                [('H02', '478.69'), ('H02a', '380.01')],
            )
            self.assertNotIn('period_start_utc', rows[0])
            write_manifest(output, {'entries': [entry], 'errors': []})
            with self.assertRaisesRegex(ValueError, 'label-only evidence'):
                ingest_manifest(output / 'manifest.json', conn=object())

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


class CaptureReferenceTest(unittest.TestCase):
    def test_duplicate_fixing_group_cannot_create_reference(self):
        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        source = fixture.read_text(encoding="utf-8")
        self.assertEqual(len(extract_reference(source.encode(), "2026-11-02")), 24)
        html = source.replace(">Notowania ciągłe</th>", ">Fixing I</th>", 1)
        self.assertNotEqual(html, source)
        with self.assertRaisesRegex(ValueError, "one two-column Fixing I group"):
            extract_reference(html.encode(), "2026-11-02")

    def test_missing_price_cell_cannot_create_reference(self):
        from lxml import html

        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        document = html.fromstring(fixture.read_text(encoding="utf-8"))
        row, = document.xpath('//table[@id="rdn"]/tbody/tr[td[1]="2026-11-02_H01"]')
        cells = row.xpath("./td")
        cells[3].text = "2492,98"
        row.remove(cells[2])
        with self.assertRaisesRegex(ValueError, "reference row does not match header"):
            extract_reference(html.tostring(document), "2026-11-02")

    def test_header_span_cannot_shift_fixing_i_group(self):
        from lxml import html

        fixture = Path(__file__).parent / "fixtures/tge_rdn_captured_2026-11-01.html"
        document = html.fromstring(fixture.read_text(encoding="utf-8"))
        first, = document.xpath('//table[@id="rdn"]/thead/tr[1]/th[1]')
        first.set("colspan", "2")
        with self.assertRaisesRegex(ValueError, "Unsupported reference header spans"):
            extract_reference(html.tostring(document), "2026-11-02")
