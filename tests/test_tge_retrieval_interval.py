"""TGE retrieval interval evidence is explicit; old captures are never backdated."""
from datetime import date, datetime, timedelta
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_ingest_manifest import load_batches
from tools.capture_tge_validation import capture, write_manifest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/tge_delivery_2026-11-26'
DAY = date(2026, 11, 26)


class TGERetrievalIntervalTests(unittest.TestCase):
    def test_capture_records_start_before_fetch_and_keeps_completion_as_actual_availability(self):
        original = json.loads((DIRECTORY / 'manifest.json').read_text())['entries'][0]
        raw = gzip.decompress((DIRECTORY / original['raw_file']).read_bytes())
        completed = datetime.fromisoformat(original['retrieved_at_utc'])
        started = completed - timedelta(seconds=7, microseconds=123)
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_tge_validation.datetime', wraps=datetime
        ) as clock, patch('tools.capture_tge_validation._fetch_session') as fetch:
            clock.now.side_effect = [started, completed]
            def response(url, day):
                self.assertEqual(clock.now.call_count, 1)
                self.assertEqual((url, day), (original['url'], DAY))
                return 200, raw
            fetch.side_effect = response
            output = Path(temporary)
            entry = capture(DAY, output)
            self.assertEqual(clock.now.call_count, 2)
            self.assertEqual(entry['retrieval_started_at_utc'], started.isoformat())
            self.assertEqual(entry['retrieved_at_utc'], completed.isoformat())
            self.assertEqual(entry['raw_sha256'], original['raw_sha256'])
            self.assertEqual(entry['reference_sha256'], original['reference_sha256'])
            write_manifest(output, dict(entries=[entry], errors=[]))
            (batch, loaded), = load_batches(output / 'manifest.json')
            self.assertEqual(loaded, raw)
            self.assertEqual(batch.retrieved_at, completed)

    def test_backward_clock_is_rejected_before_extraction_or_evidence_write(self):
        completed = datetime.fromisoformat('2026-11-25T10:15:02.707749+00:00')
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_tge_validation.datetime', wraps=datetime
        ) as clock, patch('tools.capture_tge_validation._fetch_session', return_value=(200, b'HTML')), patch(
            'tools.capture_tge_validation.extract_reference'
        ) as extract:
            clock.now.side_effect = [completed + timedelta(microseconds=1), completed]
            with self.assertRaisesRegex(ValueError, 'retrieval started after completion'):
                capture(DAY, Path(temporary))
            extract.assert_not_called()
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_optional_start_is_strictly_validated_before_payload_and_legacy_batch_stays_identical(self):
        original_bytes = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        manifest = json.loads(original_bytes[DIRECTORY / 'manifest.json'])
        original = manifest['entries'][0]
        self.assertNotIn('retrieval_started_at_utc', original)
        expected = load_batches(DIRECTORY / 'manifest.json')
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            for path, content in original_bytes.items():
                (output / path.name).write_bytes(content)
            path = output / 'manifest.json'
            for start in ('2026-11-25T10:15:02.707749+00:00',
                          '2026-11-25T11:15:02.707749+01:00',
                          '2026-11-25T08:49:59+00:00'):
                manifest['entries'][0] = {**original, 'retrieval_started_at_utc': start}
                path.write_text(json.dumps(manifest))
                self.assertEqual(load_batches(path), expected)
            for start, reason in (
                (None, 'aware Fix1 retrieval start'), (True, 'aware Fix1 retrieval start'),
                (123, 'aware Fix1 retrieval start'), ([], 'aware Fix1 retrieval start'),
                ('2026-11-25', 'aware Fix1 retrieval start'),
                ('2026-11-25T10:15:02.707749', 'aware Fix1 retrieval start'),
                ('2026-11-25T10:15:02.7077491+00:00', 'aware Fix1 retrieval start'),
                ('2026-11-25T10:15:02.707750+00:00', 'retrieval started after completion'),
            ):
                manifest['entries'][0] = {**original, 'retrieval_started_at_utc': start}
                path.write_text(json.dumps(manifest))
                before = {p: p.read_bytes() for p in output.iterdir()}
                with self.subTest(start=start), patch(
                    'shadow_ingest_manifest._inside', side_effect=AssertionError('Payload accessed')
                ) as inside:
                    with self.assertRaisesRegex(ValueError, reason):
                        load_batches(path)
                    inside.assert_not_called()
                self.assertEqual({p: p.read_bytes() for p in output.iterdir()}, before)
        self.assertEqual({p: p.read_bytes() for p in original_bytes}, original_bytes)


if __name__ == '__main__':
    unittest.main()
