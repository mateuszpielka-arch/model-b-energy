"""Joint audit CLI uses retained evidence and never emits a partial report."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_source_coverage import audit_source_coverage

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures'
CAPTURES = [FIXTURE / ('pse_pk5_delivery_2026-11-25_' + suffix) / 'capture.json'
            for suffix in ('seventh', 'eighth')]
MANIFEST = FIXTURE / 'tge_delivery_2026-11-24/manifest.json'


class JointCoverageCLITests(unittest.TestCase):
    def run_cli(self, captures=CAPTURES, manifests=(MANIFEST,), extra=()):
        command = [sys.executable, str(ROOT / 'shadow_source_coverage.py'),
                   '--start-day', '2026-11-24', '--end-day', '2026-11-25',
                   '--source-day', '2026-11-25', *map(str, captures)]
        for manifest in manifests:
            command += ['--fix1-manifest', str(manifest)]
        return subprocess.run(command + list(extra), capture_output=True, text=True)

    def test_real_pk5_and_fix1_report_matches_api_in_both_orders_without_writes(self):
        files = {p: p.read_bytes() for directory in {MANIFEST.parent, *[p.parent for p in CAPTURES]}
                 for p in directory.iterdir() if p.is_file()}
        expected = audit_source_coverage(
            pk5_groups=[dict(day=date(2026, 11, 25), captures=CAPTURES)],
            fix1_manifests=[MANIFEST], start_day=date(2026, 11, 24), end_day=date(2026, 11, 25))
        for captures in (CAPTURES, list(reversed(CAPTURES))):
            result = self.run_cli(captures, extra=['--require-selection'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            report = json.loads(result.stdout)
            self.assertEqual(report, expected)
            self.assertTrue(report['read_only'])
            self.assertFalse(report['model_readiness_assessed'])
            self.assertEqual(report['source_coverage']['pk5_only_days'], ['2026-11-25'])
            self.assertEqual(report['source_coverage']['fix1_only_days'], ['2026-11-24'])
            self.assertEqual(report['source_coverage']['both_source_days'], [])
            self.assertEqual(report['pk5_audit']['days'][0]['selected_snapshot_id'],
                             'pk5-capture-sha256:5a6a8f9d84c3f487924a0a2061e7ab28f92f047e2cb23148e9675fa958591e38')
            self.assertEqual(report['pk5_audit']['days'][0]['cutoff_utc'],
                             '2026-11-24T08:50:00+00:00')
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_duplicate_real_capture_is_diagnostic_until_selection_is_required(self):
        captures = [CAPTURES[-1], CAPTURES[-1]]
        normal = self.run_cli(captures)
        strict = self.run_cli(captures, extra=['--require-selection'])
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(strict.returncode, 3, strict.stderr)
        self.assertEqual(normal.stderr, '')
        self.assertEqual(strict.stderr, '')
        report = json.loads(strict.stdout)
        self.assertEqual(json.loads(normal.stdout), report)
        self.assertIsNone(report['pk5_audit']['days'][0]['selected_snapshot_id'])
        self.assertEqual(report['source_coverage']['pk5_only_days'], [])
        self.assertEqual(report['source_coverage']['neither_source_days'], ['2026-11-25'])

    def test_invalid_sources_and_bounds_emit_no_partial_report(self):
        absent = FIXTURE / 'does-not-exist.json'
        cases = [
            dict(captures=[CAPTURES[0], absent]),
            dict(captures=[absent, CAPTURES[0]]),
            dict(manifests=[MANIFEST, absent]),
            dict(manifests=[absent, MANIFEST]),
            dict(extra=['--end-day', '2026-11-23']),
            dict(extra=['--start-day', '0001-01-01']),
            dict(extra=['--source-day', '20261125', str(CAPTURES[0])]),
            dict(extra=['--source-day', '2026-11-24']),
            dict(extra=['--source-day', '2026-11-25', str(CAPTURES[0])]),
        ]
        for case in cases:
            with self.subTest(case=case):
                result = self.run_cli(**case)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn('error:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
