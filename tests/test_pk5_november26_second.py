"""Two real PK5 November 26 profiles retain row-level publication evidence."""
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

from shadow_asof import cutoff_for_day
from shadow_pk5_audit import audit_pk5_captures
from shadow_source_history import load_pk5_snapshot

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 26)


class SecondNovember26Tests(unittest.TestCase):
    def test_real_revision_selects_whole_profile_with_11_and_13_publications_in_both_orders(self):
        paths = [ROOT / ('tests/fixtures/pse_pk5_delivery_2026-11-26' + suffix) / 'capture.json'
                 for suffix in ('', '_second')]
        files = {p: p.read_bytes() for path in paths for p in path.parent.iterdir() if p.is_file()}
        entries = [json.loads(p.read_text()) for p in paths]
        latest = entries[-1]
        self.assertEqual((latest['http_status'], latest['rows'], latest['bytes']), (200, 24, 18626))
        self.assertEqual(latest['response_url'], latest['url'])
        self.assertEqual(latest['raw_sha256'],
                         '2b2dd523d43a7b3f48e689c9b1f2bf714147397b1f387607d5b2b38ece83c8a3')
        self.assertEqual(latest['snapshot_id'],
                         'pk5-capture-sha256:8934706605a66edc27caf6bd4a9e57628433d21313cd106802c1865c2be778e8')
        originals = []
        for path, entry in zip(paths, entries, strict=True):
            raw = gzip.decompress((path.parent / entry['raw_file']).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
            originals.append({
                datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
                - timedelta(hours=1): (
                    Decimal(row['fcst_gen_unit_stor_prov']),
                    datetime.fromisoformat(row['publication_ts_utc']).replace(tzinfo=timezone.utc))
                for row in json.loads(raw, parse_float=Decimal)['value']})
            snapshot = load_pk5_snapshot(path, day=DAY)
            self.assertEqual(snapshot['id'], entry['snapshot_id'])
            self.assertLessEqual(snapshot['retrieved_at'], snapshot['retrieval_completed_at'])
            self.assertLessEqual(snapshot['retrieval_completed_at'], cutoff_for_day(DAY))
            for row in snapshot['rows']:
                self.assertLessEqual(row['publication_ts'], snapshot['retrieval_completed_at'])
                self.assertLessEqual(row['publication_ts'], cutoff_for_day(DAY))
        expected = audit_pk5_captures(paths, day=DAY)
        self.assertEqual(expected['selected_snapshot_id'], latest['snapshot_id'])
        self.assertEqual(expected['cutoff_utc'], '2026-11-25T08:50:00+00:00')
        self.assertEqual({
            datetime.fromisoformat(row['period_start_utc']): (
                Decimal(row['jwcd_mw']), datetime.fromisoformat(row['publication_ts_utc']))
            for row in expected['selected_rows']}, originals[-1])
        self.assertEqual(dict(Counter(r['publication_ts_utc'] for r in expected['selected_rows'])),
                         {'2026-11-24T17:03:32.118000+00:00': 11,
                          '2026-11-24T17:13:27.320000+00:00': 13})
        self.assertEqual((expected['selected_rows'][0]['jwcd_mw'],
                          expected['selected_rows'][-1]['jwcd_mw']), ('14931.00000', '15038.00000'))
        self.assertEqual(expected['rejected_captures'], [
            dict(snapshot_id=entries[0]['snapshot_id'], reason='SUPERSEDED_JWCD_PUBLICATIONS')])
        differences = {r['snapshot_id']: r for r in expected['revision_differences_from_selected']}
        changed = sorted(p.isoformat() for p in originals[0]
                         if originals[0][p][0] != originals[-1][p][0])
        self.assertEqual(len(changed), 23)
        self.assertEqual(differences[entries[0]['snapshot_id']]['changed_periods_utc'], changed)
        self.assertEqual(differences[latest['snapshot_id']]['changed_periods'], 0)
        for ordered in (paths, list(reversed(paths))):
            self.assertEqual(audit_pk5_captures(ordered, day=DAY), expected)
            result = subprocess.run(
                [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                 '--require-selection', *map(str, ordered)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == '__main__':
    unittest.main()
