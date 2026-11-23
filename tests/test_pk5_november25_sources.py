"""Retained original pre-cutoff PK5 source for November 25; no Fix1 or forecast."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from itertools import permutations
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


    def test_second_real_revision_selects_whole_newer_publication_in_both_orders(self):
        second = DIRECTORY.parent / 'pse_pk5_delivery_2026-11-25_second'
        paths = [DIRECTORY / 'capture.json', second / 'capture.json']
        files = {p: p.read_bytes() for root in (DIRECTORY, second)
                 for p in root.iterdir() if p.is_file()}
        metadata = [json.loads(path.read_text()) for path in paths]
        latest = metadata[-1]
        self.assertEqual((latest['http_status'], latest['rows'], latest['bytes']), (200, 24, 18645))
        self.assertEqual(latest['response_url'], latest['url'])
        self.assertEqual(latest['raw_sha256'],
                         'd79a3599e74f5802fc5ab3bb1805afa509b01aa4550d11bf93f0bc00b43867f7')
        self.assertEqual(latest['snapshot_id'],
                         'pk5-capture-sha256:0cbbb9cbc0710615ea462198bb1f834023c2b8a0548eec64707260133577ad13')
        original_maps = []
        snapshots = []
        for path, entry in zip(paths, metadata, strict=True):
            raw = gzip.decompress((path.parent / entry['raw_file']).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
            payload = json.loads(raw, parse_float=Decimal)['value']
            original_maps.append({
                datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
                - timedelta(hours=1): Decimal(row['fcst_gen_unit_stor_prov'])
                for row in payload})
            snapshots.append(prepare_pk5_snapshot(
                day=DAY.isoformat(), **load_pk5_capture(path, day=DAY)))
        changed = sorted(period.isoformat() for period in original_maps[0]
                         if original_maps[0][period] != original_maps[1][period])
        self.assertEqual(len(changed), 7)
        expected_rows = [dict(period_start_utc=row['period_start_utc'].isoformat(),
                              jwcd_mw=str(row['jwcd_mw']),
                              publication_ts_utc=row['publication_ts'].isoformat())
                         for row in sorted(snapshots[-1]['rows'],
                                           key=lambda row: row['period_start_utc'])]
        expected = audit_pk5_captures(paths, day=DAY)
        self.assertEqual(expected['selected_snapshot_id'], latest['snapshot_id'])
        self.assertEqual(expected['selected_rows'], expected_rows)
        self.assertEqual({
            datetime.fromisoformat(row['period_start_utc']): Decimal(row['jwcd_mw'])
            for row in expected['selected_rows']
        }, original_maps[1])
        self.assertEqual(expected['selected_rows'][0]['jwcd_mw'], '12791.00000')
        self.assertEqual(expected['selected_rows'][-1]['jwcd_mw'], '14185.00000')
        self.assertEqual({row['publication_ts_utc'] for row in expected_rows},
                         {'2026-11-23T21:05:12.606000+00:00'})
        self.assertEqual(expected['rejected_captures'], [dict(
            snapshot_id=metadata[0]['snapshot_id'], reason='SUPERSEDED_JWCD_PUBLICATIONS')])
        changes = {item['snapshot_id']: item for item in expected['revision_differences_from_selected']}
        self.assertEqual(changes[metadata[0]['snapshot_id']]['changed_periods_utc'], changed)
        self.assertEqual(changes[latest['snapshot_id']]['changed_periods'], 0)
        for ordering in (paths, list(reversed(paths))):
            self.assertEqual(audit_pk5_captures(ordering, day=DAY), expected)
            completed = subprocess.run(
                [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                 '--require-selection', *map(str, ordering)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, '')
            self.assertEqual(json.loads(completed.stdout), expected)
        self.assertEqual({p: p.read_bytes() for p in files}, files)


    def test_third_real_revision_replaces_all_24_values_in_every_capture_order(self):
        paths = [DIRECTORY.parent / ('pse_pk5_delivery_2026-11-25' + suffix) / 'capture.json'
                 for suffix in ('', '_second', '_third')]
        files = {p: p.read_bytes() for path in paths
                 for p in path.parent.iterdir() if p.is_file()}
        metadata = [json.loads(path.read_text()) for path in paths]
        latest = metadata[-1]
        self.assertEqual((latest['http_status'], latest['rows'], latest['bytes']), (200, 24, 18646))
        self.assertEqual(latest['response_url'], latest['url'])
        self.assertEqual(latest['raw_sha256'],
                         'bf1d0ce19d44913440e949c374071ac5c5fc1d9fd32f6faf4e31c7cd146884e1')
        self.assertEqual(latest['snapshot_id'],
                         'pk5-capture-sha256:55689b2cccb61e5667821a7a8317aa4eadc5aff88cfa80905d541007b5380e6b')
        original_maps = []
        for path, entry in zip(paths, metadata, strict=True):
            raw = gzip.decompress((path.parent / entry['raw_file']).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
            payload = json.loads(raw, parse_float=Decimal)['value']
            original_maps.append({
                datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
                - timedelta(hours=1): Decimal(row['fcst_gen_unit_stor_prov'])
                for row in payload})
            load_pk5_capture(path, day=DAY)
        report = audit_pk5_captures(paths, day=DAY)
        self.assertEqual(report['selected_snapshot_id'], latest['snapshot_id'])
        self.assertEqual({
            datetime.fromisoformat(row['period_start_utc']): Decimal(row['jwcd_mw'])
            for row in report['selected_rows']
        }, original_maps[-1])
        self.assertEqual({row['publication_ts_utc'] for row in report['selected_rows']},
                         {'2026-11-23T23:14:26.244000+00:00'})
        self.assertEqual(report['selected_rows'][0]['jwcd_mw'], '12765.00000')
        self.assertEqual(report['selected_rows'][-1]['jwcd_mw'], '14232.00000')
        self.assertEqual({item['snapshot_id'] for item in report['rejected_captures']},
                         {entry['snapshot_id'] for entry in metadata[:-1]})
        self.assertEqual({item['reason'] for item in report['rejected_captures']},
                         {'SUPERSEDED_JWCD_PUBLICATIONS'})
        changes = {item['snapshot_id']: item for item in report['revision_differences_from_selected']}
        for entry, old in zip(metadata[:-1], original_maps[:-1], strict=True):
            changed = sorted(period.isoformat() for period in old
                             if old[period] != original_maps[-1][period])
            self.assertEqual(len(changed), 24)
            self.assertEqual(changes[entry['snapshot_id']]['changed_periods_utc'], changed)
        self.assertEqual(changes[latest['snapshot_id']]['changed_periods'], 0)
        for ordering in permutations(paths):
            self.assertEqual(audit_pk5_captures(ordering, day=DAY), report)
        for ordering in (paths, list(reversed(paths))):
            result = subprocess.run(
                [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                 '--require-selection', *map(str, ordering)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), report)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

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
