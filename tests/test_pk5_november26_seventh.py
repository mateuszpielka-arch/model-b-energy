"""Seven real PK5 November 26 profiles retain row-level publication evidence."""
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


class SeventhNovember26Tests(unittest.TestCase):
    def test_seventh_real_profile_preserves_split_publications_at_each_position(self):
        paths = [ROOT / ('tests/fixtures/pse_pk5_delivery_2026-11-26' + suffix) / 'capture.json'
                 for suffix in ('', '_second', '_third', '_fourth', '_fifth', '_sixth', '_seventh')]
        files = {p: p.read_bytes() for path in paths for p in path.parent.iterdir() if p.is_file()}
        entries = [json.loads(p.read_text()) for p in paths]
        latest = entries[-1]
        self.assertEqual((latest['http_status'], latest['rows'], latest['bytes']), (200, 24, 18612))
        self.assertEqual(latest['response_url'], latest['url'])
        self.assertEqual(latest['raw_sha256'],
                         '2851732b4c7d568ba84832ef86c1d80c3d081d430013c3da4c4b5bbc11cc1ba0')
        self.assertEqual(latest['snapshot_id'],
                         'pk5-capture-sha256:37e87f0ee7dfa033979d980d110f47e9a1e3218d8f58dee24e5e90b26e00062d')
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
                         {'2026-11-25T02:03:57.130000+00:00': 9,
                          '2026-11-25T02:13:17.023000+00:00': 15})
        self.assertEqual((expected['selected_rows'][0]['jwcd_mw'],
                          expected['selected_rows'][-1]['jwcd_mw']), ('15168.00000', '15311.00000'))
        self.assertEqual({r['snapshot_id'] for r in expected['rejected_captures']},
                         {entry['snapshot_id'] for entry in entries[:-1]})
        self.assertEqual({r['reason'] for r in expected['rejected_captures']},
                         {'SUPERSEDED_JWCD_PUBLICATIONS'})
        differences = {r['snapshot_id']: r for r in expected['revision_differences_from_selected']}
        for entry, original, count in zip(entries[:-1], originals[:-1], (24, 24, 24, 24, 24, 16), strict=True):
            changed = sorted(p.isoformat() for p in original
                             if original[p][0] != originals[-1][p][0])
            self.assertEqual(len(changed), count)
            self.assertEqual(differences[entry['snapshot_id']]['changed_periods_utc'], changed)
        self.assertEqual(differences[latest['snapshot_id']]['changed_periods'], 0)
        checked = 0
        for prior in (paths[:-1], list(reversed(paths[:-1]))):
            for position in range(len(paths)):
                ordered = prior[:position] + [paths[-1]] + prior[position:]
                self.assertEqual(audit_pk5_captures(ordered, day=DAY), expected)
                checked += 1
        self.assertEqual(checked, 14)
        for ordered in (paths, list(reversed(paths))):
            result = subprocess.run(
                [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                 '--require-selection', *map(str, ordered)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == '__main__':
    unittest.main()
