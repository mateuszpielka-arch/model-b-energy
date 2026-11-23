"""Retained original pre-cutoff PK5 source for November 25; no Fix1 or forecast."""
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
from shadow_source_history import load_pk5_capture


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-25'
DAY = date(2026, 11, 25)


class November25PK5SourceTests(unittest.TestCase):
    def test_real_capture_matches_original_jwcd_labels_utc_and_pre_cutoff_publication(self):
        files = {path: path.read_bytes() for path in DIRECTORY.iterdir() if path.is_file()}
        path = DIRECTORY / 'capture.json'
        metadata = json.loads(path.read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        self.assertEqual((metadata['http_status'], metadata['rows'], metadata['bytes']), (200, 24, 18645))
        self.assertEqual(metadata['response_url'], metadata['url'])
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(metadata['raw_sha256'],
                         '3b0386623f9984e6a5be31d538adbd27cf5521c360fd0a135435421cce7c78af')
        capture = load_pk5_capture(path, day=DAY)
        self.assertEqual(capture['raw'], raw)
        snapshot = prepare_pk5_snapshot(day=DAY.isoformat(), **capture)
        selected = select_snapshot(DAY, [snapshot])
        self.assertEqual(selected.snapshot_id, metadata['snapshot_id'])
        self.assertEqual(selected.snapshot_id,
                         'pk5-capture-sha256:319f72ac7c9d463b433577cd10982f146e72aef85b5896b2af569a0560bb3c4d')
        self.assertEqual(selected.cutoff_utc.isoformat(), '2026-11-24T08:50:00+00:00')
        self.assertLessEqual(selected.latest_publication_ts_utc, selected.retrieval_completed_at_utc)
        self.assertLessEqual(selected.retrieved_at_utc, selected.retrieval_completed_at_utc)
        self.assertLessEqual(selected.retrieval_completed_at_utc, selected.cutoff_utc)
        payload = json.loads(raw, parse_float=Decimal)['value']
        self.assertEqual([row['period'] for row in payload],
                         [f'{hour:02d} - {hour+1:02d}' for hour in range(24)])
        self.assertEqual({row['business_date'] for row in payload}, {'2026-11-25'})
        # The PSE UTC timestamp is an interval end; the canonical key is its start.
        expected = tuple(sorted(
            (datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
             - timedelta(hours=1), Decimal(row['fcst_gen_unit_stor_prov']))
            for row in payload))
        self.assertEqual(selected.rows, expected)
        self.assertEqual(selected.rows[0][1], Decimal('12794.00000'))
        self.assertEqual(selected.rows[-1][1], Decimal('14185.00000'))
        self.assertEqual(dict(Counter(row['publication_ts_utc'] for row in payload)),
                         {'2026-11-23 20:03:47.599': 24})
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

    def test_synthetic_late_completion_or_publication_cannot_admit_real_capture_values(self):
        path = DIRECTORY / 'capture.json'
        before = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        snapshot = prepare_pk5_snapshot(day=DAY.isoformat(), **load_pk5_capture(path, day=DAY))
        late = cutoff_for_day(DAY) + timedelta(microseconds=1)
        late_rows = ({**snapshot['rows'][0], 'publication_ts': late}, *snapshot['rows'][1:])
        cases = (
            ({**snapshot, 'id': 'synthetic:late-completion', 'retrieval_completed_at': late},
             'RETRIEVAL_COMPLETED_AFTER_CUTOFF'),
            ({**snapshot, 'id': 'synthetic:late-publication', 'rows': late_rows},
             'PUBLICATION_AFTER_CUTOFF'),
        )
        for candidate, reason in cases:
            with self.subTest(reason=reason):
                result = select_snapshot(DAY, [candidate])
                self.assertIsNone(result.snapshot_id)
                self.assertEqual(result.rows, ())
                self.assertEqual(result.rejected, ((candidate['id'], reason),))
        self.assertEqual({p: p.read_bytes() for p in before}, before)


if __name__ == '__main__':
    unittest.main()
