from datetime import date, datetime, timedelta
from hashlib import sha256
from itertools import permutations
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_asof import cutoff_for_day
from shadow_pk5_audit import audit_pk5_captures


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'


class PK5AuditTests(unittest.TestCase):
    def paths(self):
        return [FIXTURES / ('pse_pk5_delivery_2026-11-22' + suffix) / 'capture.json'
                for suffix in ('', '_second', '_third', '_fourth')]

    def cli(self, paths, day='2026-11-22'):
        return subprocess.run([sys.executable, str(ROOT / 'shadow_pk5_audit.py'),
                               '--day', day, *map(str, paths)],
                              capture_output=True, text=True)

    def test_four_real_revisions_report_selection_and_mixed_publications_in_every_order(self):
        paths = self.paths()
        files = {path for capture in paths for path in capture.parent.iterdir() if path.is_file()}
        before = {path: sha256(path.read_bytes()).hexdigest() for path in files}
        report = audit_pk5_captures(paths, day=date(2026, 11, 22))
        metadata = [json.loads(path.read_text()) for path in paths]
        self.assertTrue(report['read_only'])
        self.assertEqual(report['selected_snapshot_id'], metadata[-1]['snapshot_id'])
        self.assertEqual(report['cutoff_utc'], '2026-11-21T08:50:00+00:00')
        self.assertEqual(len(report['selected_rows']), 24)
        self.assertEqual((report['selected_rows'][0]['jwcd_mw'],
                          report['selected_rows'][-1]['jwcd_mw']), ('6326.00000', '9744.00000'))
        mixed, = [item for item in report['captures']
                  if item['snapshot_id'] == metadata[1]['snapshot_id']]
        self.assertEqual(mixed['publication_counts_utc'],
                         {'2026-11-21T01:03:06.832000+00:00': 10,
                          '2026-11-21T01:12:31.438000+00:00': 14})
        self.assertEqual(len(report['rejected_captures']), 3)
        self.assertEqual({item['reason'] for item in report['rejected_captures']},
                         {'SUPERSEDED_JWCD_PUBLICATIONS'})
        for ordering in permutations(paths):
            self.assertEqual(audit_pk5_captures(ordering, day=date(2026, 11, 22)), report)
        for ordering in (paths, list(reversed(paths))):
            completed = self.cli(ordering)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, '')
            self.assertEqual(json.loads(completed.stdout), report)
        self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in files}, before)

    def test_early_capture_without_fix1_and_duplicate_diagnostics_do_not_claim_readiness(self):
        path = FIXTURES / 'pse_pk5_delivery_2026-11-24/capture.json'
        report = audit_pk5_captures([path], day=date(2026, 11, 24))
        self.assertEqual(report['cutoff_utc'], '2026-11-23T08:50:00+00:00')
        self.assertEqual(len(report['selected_rows']), 24)
        self.assertNotIn('model_ready', report)
        self.assertEqual(report['rejected_captures'], [])
        duplicate = audit_pk5_captures([path, path], day=date(2026, 11, 24))
        self.assertIsNone(duplicate['selected_snapshot_id'])
        self.assertEqual(duplicate['selected_rows'], [])
        self.assertEqual({item['reason'] for item in duplicate['rejected_captures']},
                         {'MISSING_OR_DUPLICATE_SNAPSHOT_ID'})
        completed = self.cli([path, path], day='2026-11-24')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), duplicate)

    def test_invalid_or_late_capture_aborts_every_order_without_partial_json_or_writes(self):
        good = self.paths()[-1]
        metadata = json.loads(good.read_text())
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'invalid'
            shutil.copytree(good.parent, target)
            path = target / 'capture.json'
            for changed in (
                '{',
                json.dumps({**metadata, 'raw_sha256': '0' * 64}),
                json.dumps({**metadata, 'retrieval_completed_at_utc':
                            (cutoff_for_day(date(2026, 11, 22)) +
                             timedelta(microseconds=1)).isoformat()}),
            ):
                path.write_text(changed)
                before = {item: item.read_bytes() for item in target.iterdir()}
                for paths in ([good, path], [path, good]):
                    with self.assertRaises(ValueError):
                        audit_pk5_captures(paths, day=date(2026, 11, 22))
                    completed = self.cli(paths)
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, '')
                    self.assertNotIn('Traceback', completed.stderr)
                self.assertEqual({item: item.read_bytes() for item in target.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
