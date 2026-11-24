"""Six retained real PK5 observations select one whole pre-cutoff November 25 profile."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_pk5_audit import audit_pk5_captures
from shadow_source_history import load_pk5_snapshot


ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 25)


class SixthNovember25PK5Tests(unittest.TestCase):
    def test_sixth_real_mixed_publication_profile_matches_sources_in_all_720_orders(self):
        paths = [ROOT / ('tests/fixtures/pse_pk5_delivery_2026-11-25' + suffix) / 'capture.json'
                 for suffix in ('', '_second', '_third', '_fourth', '_fifth', '_sixth')]
        files = {p: p.read_bytes() for path in paths for p in path.parent.iterdir() if p.is_file()}
        metadata = [json.loads(path.read_text()) for path in paths]
        latest = metadata[-1]
        self.assertEqual((latest['http_status'], latest['rows'], latest['bytes']), (200, 24, 18641))
        self.assertEqual(latest['response_url'], latest['url'])
        self.assertEqual(latest['raw_sha256'],
                         '95cd2852bd810ed723dcfca17bc57d5125c58a26aca2b90acbed1511ad4398e7')
        self.assertEqual(latest['snapshot_id'],
                         'pk5-capture-sha256:729a97def009adcb5d37fae268857cd97fa35df9b23ce100ba55b32d0066fd5a')
        originals = []
        for path, entry in zip(paths, metadata, strict=True):
            raw = gzip.decompress((path.parent / entry['raw_file']).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
            originals.append({
                datetime.fromisoformat(row['plan_dtime_utc']).replace(tzinfo=timezone.utc)
                - timedelta(hours=1): (
                    Decimal(row['fcst_gen_unit_stor_prov']),
                    datetime.fromisoformat(row['publication_ts_utc']).replace(tzinfo=timezone.utc))
                for row in json.loads(raw, parse_float=Decimal)['value']})
            self.assertEqual(load_pk5_snapshot(path, day=DAY)['id'], entry['snapshot_id'])
        report = audit_pk5_captures(paths, day=DAY)
        self.assertEqual(report['selected_snapshot_id'], latest['snapshot_id'])
        self.assertEqual({
            datetime.fromisoformat(row['period_start_utc']): (
                Decimal(row['jwcd_mw']), datetime.fromisoformat(row['publication_ts_utc']))
            for row in report['selected_rows']
        }, originals[-1])
        self.assertEqual(dict(Counter(row['publication_ts_utc'] for row in report['selected_rows'])),
                         {'2026-11-24T05:04:07.697000+00:00': 12,
                          '2026-11-24T05:13:17.249000+00:00': 12})
        self.assertEqual(report['selected_rows'][0]['jwcd_mw'], '12796.00000')
        self.assertEqual(report['selected_rows'][-1]['jwcd_mw'], '14418.00000')
        self.assertEqual({item['snapshot_id'] for item in report['rejected_captures']},
                         {entry['snapshot_id'] for entry in metadata[:-1]})
        self.assertEqual({item['reason'] for item in report['rejected_captures']},
                         {'SUPERSEDED_JWCD_PUBLICATIONS'})
        differences = {item['snapshot_id']: item
                       for item in report['revision_differences_from_selected']}
        for entry, old, count in zip(metadata[:-1], originals[:-1], (24, 24, 24, 24, 24), strict=True):
            changed = sorted(period.isoformat() for period in old
                             if old[period][0] != originals[-1][period][0])
            self.assertEqual(len(changed), count)
            self.assertEqual(differences[entry['snapshot_id']]['changed_periods_utc'], changed)
        self.assertEqual(differences[latest['snapshot_id']]['changed_periods'], 0)
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


if __name__ == '__main__':
    unittest.main()
