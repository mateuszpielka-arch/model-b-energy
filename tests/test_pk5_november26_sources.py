"""Retained original pre-cutoff PK5 source for November 26; no Fix1 or forecast."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_pk5_audit import audit_pk5_captures
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_source_history import load_pk5_capture, load_pk5_snapshot
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-26'
DAY = date(2026, 11, 26)


class November26PK5SourceTests(unittest.TestCase):
    def test_real_capture_matches_original_jwcd_labels_utc_and_pre_cutoff_publication(self):
        files = {path: path.read_bytes() for path in DIRECTORY.iterdir() if path.is_file()}
        path = DIRECTORY / 'capture.json'
        metadata = json.loads(path.read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18626))
        self.assertEqual(metadata['response_url'], metadata['url'])
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(metadata['raw_sha256'],
                         'e1478f352a662a5e7c79e54143a0c1b5cc688cbb3d547f900198923aab3b0c9a')
        capture = load_pk5_capture(path, day=DAY)
        self.assertEqual(capture['raw'], raw)
        snapshot = prepare_pk5_snapshot(day=DAY.isoformat(), **capture)
        selected = select_snapshot(DAY, [snapshot])
        self.assertEqual(selected.snapshot_id, metadata['snapshot_id'])
        self.assertEqual(selected.snapshot_id,
                         'pk5-capture-sha256:5f4e085ac37c4852d6207a8f391bacb3037eb1382ee5cb4e4b46a7aac7dba873')
        self.assertEqual(selected.cutoff_utc.isoformat(), '2026-11-25T08:50:00+00:00')
        self.assertLessEqual(selected.latest_publication_ts_utc, selected.retrieval_completed_at_utc)
        self.assertLessEqual(selected.retrieved_at_utc, selected.retrieval_completed_at_utc)
        self.assertLessEqual(selected.retrieval_completed_at_utc, selected.cutoff_utc)
        payload = json.loads(raw, parse_float=Decimal)['value']
        self.assertEqual([row['period'] for row in payload],
                         [f'{hour:02d} - {hour+1:02d}' for hour in range(24)])
        self.assertEqual({row['business_date'] for row in payload}, {'2026-11-26'})
        # The PSE UTC timestamp is an interval end; the canonical key is its start.
        expected = tuple(sorted(
            (datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
             - timedelta(hours=1), Decimal(row['fcst_gen_unit_stor_prov']))
            for row in payload))
        self.assertEqual(selected.rows, expected)
        self.assertEqual(selected.rows[0][1], Decimal('14927.00000'))
        self.assertEqual(selected.rows[-1][1], Decimal('15041.00000'))
        self.assertEqual(dict(Counter(row['publication_ts_utc'] for row in payload)),
                         {'2026-11-24 16:03:47.487': 24})
        report = audit_pk5_captures([path], day=DAY)
        self.assertEqual(report['selected_snapshot_id'], selected.snapshot_id)
        self.assertEqual(report['rejected_captures'], [])
        self.assertEqual(len(report['selected_rows']), 24)
        self.assertNotIn('model_ready', report)
        completed = subprocess.run(
            [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
             '--require-selection', str(path)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, '')
        self.assertEqual(json.loads(completed.stdout), report)
        self.assertEqual({path: path.read_bytes() for path in files}, files)


    def test_temporary_retrieval_boundary_accepts_cutoff_and_rejects_one_microsecond_later(self):
        """Synthetic metadata only; do not backdate or change retained evidence."""
        source = DIRECTORY / 'capture.json'
        original = json.loads(source.read_text())
        compressed = (DIRECTORY / original['raw_file']).read_bytes()
        cutoff = cutoff_for_day(DAY)
        with TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / original['raw_file']).write_bytes(compressed)
            path = target / 'capture.json'
            metadata = {key: value for key, value in original.items() if key != 'snapshot_id'}
            metadata['retrieved_at_utc'] = (cutoff - timedelta(microseconds=1)).isoformat()
            metadata['retrieval_completed_at_utc'] = cutoff.isoformat()
            path.write_text(json.dumps(metadata))
            snapshot = load_pk5_snapshot(path, day=DAY)
            self.assertEqual(snapshot['retrieval_completed_at'], cutoff)
            self.assertEqual(len(snapshot['rows']), 24)
            metadata['retrieval_completed_at_utc'] = (cutoff + timedelta(microseconds=1)).isoformat()
            path.write_text(json.dumps(metadata))
            before = {p: p.read_bytes() for p in target.iterdir()}
            for loader in (load_pk5_capture, load_pk5_snapshot):
                with self.assertRaisesRegex(ValueError, 'RETRIEVAL_COMPLETED_AFTER_CUTOFF'):
                    loader(path, day=DAY)
            result = subprocess.run(
                [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                 '--require-selection', str(source), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertIn('RETRIEVAL_COMPLETED_AFTER_CUTOFF', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)
        self.assertEqual((DIRECTORY / original['raw_file']).read_bytes(), compressed)


if __name__ == '__main__':
    unittest.main()
