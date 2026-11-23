"""Multi-day audit uses original PK5 evidence without any Fix1 package."""
from datetime import date, datetime
from itertools import permutations, product
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_pk5_audit import audit_pk5_captures
from shadow_pk5_history_audit import _calendar_coverage, audit_pk5_history

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'


class PK5HistoryAuditTests(unittest.TestCase):
    def groups(self):
        return [
            dict(day=date(2026, 11, 22), captures=[
                FIXTURES / ('pse_pk5_delivery_2026-11-22' + suffix) / 'capture.json'
                for suffix in ('_second', '_fourth')]),
            dict(day=date(2026, 11, 24), captures=[
                FIXTURES / ('pse_pk5_delivery_2026-11-24' + suffix) / 'capture.json'
                for suffix in ('', '_sixth')]),
        ]

    def cli(self, groups, strict=False):
        args = [sys.executable, str(ROOT / 'shadow_pk5_history_audit.py')]
        for group in groups:
            args.extend(['--source-day', group['day'].isoformat(), *map(str, group['captures'])])
        if strict:
            args.append('--require-selection')
        return subprocess.run(args, capture_output=True, text=True)

    def test_real_days_retain_whole_capture_reports_and_explicit_gap_in_every_order(self):
        groups = self.groups()
        files = {path for group in groups for capture in group['captures']
                 for path in capture.parent.iterdir() if path.is_file()}
        before = {path: path.read_bytes() for path in files}
        report = audit_pk5_history(groups)
        self.assertTrue(report['read_only'])
        self.assertFalse(report['model_readiness_assessed'])
        self.assertEqual((report['observed_days'], report['selected_days'], report['unselected_days']),
                         (2, 2, 0))
        self.assertEqual((report['observed_periods'], report['selected_periods']), (48, 48))
        self.assertEqual(report['missing_delivery_ranges'],
                         [dict(start_day='2026-11-23', end_day='2026-11-23', days=1)])
        self.assertEqual(report['calendar_coverage_by_hours']['24'], dict(
            calendar_days=3, absent_days=1, observed_unselected_days=0,
            selected_days=2, calendar_periods=72, selected_periods=48, uncovered_periods=24))
        for hours in ('23', '25'):
            self.assertEqual(set(report['calendar_coverage_by_hours'][hours].values()), {0})
        self.assertEqual(report['days'], [audit_pk5_captures(g['captures'], day=g['day'])
                                          for g in groups])
        self.assertEqual([item['cutoff_utc'] for item in report['days']],
                         ['2026-11-21T08:50:00+00:00', '2026-11-23T08:50:00+00:00'])
        for captures in product(*(list(permutations(g['captures'])) for g in groups)):
            reordered = [dict(day=g['day'], captures=list(paths))
                         for g, paths in zip(groups, captures, strict=True)]
            for ordering in permutations(reordered):
                self.assertEqual(audit_pk5_history(list(ordering)), report)
        for ordering in (groups, list(reversed(groups))):
            completed = self.cli(ordering, strict=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, '')
            self.assertEqual(json.loads(completed.stdout), report)
        self.assertEqual({path: path.read_bytes() for path in files}, before)

    def test_unselected_day_keeps_full_diagnostics_and_strict_exit_three(self):
        groups = self.groups()
        groups[-1]['captures'] = [groups[-1]['captures'][-1]] * 2
        report = audit_pk5_history(groups)
        self.assertEqual((report['selected_days'], report['unselected_days']), (1, 1))
        self.assertEqual(report['selected_periods'], 24)
        self.assertEqual(report['calendar_coverage_by_hours']['24'], dict(
            calendar_days=3, absent_days=1, observed_unselected_days=1,
            selected_days=1, calendar_periods=72, selected_periods=24, uncovered_periods=48))
        self.assertIsNone(report['days'][-1]['selected_snapshot_id'])
        self.assertEqual(report['days'][-1]['selected_rows'], [])
        self.assertEqual({item['reason'] for item in report['days'][-1]['rejected_captures']},
                         {'MISSING_OR_DUPLICATE_SNAPSHOT_ID'})
        diagnostic = self.cli(groups)
        strict = self.cli(groups, strict=True)
        self.assertEqual(diagnostic.returncode, 0, diagnostic.stderr)
        self.assertEqual(strict.returncode, 3, strict.stderr)
        self.assertEqual(strict.stderr, '')
        self.assertEqual(strict.stdout, diagnostic.stdout)
        self.assertEqual(json.loads(strict.stdout), report)

    def test_invalid_capture_aborts_all_output_and_preserves_sources_in_both_orders(self):
        groups = self.groups()
        with TemporaryDirectory() as directory:
            target = Path(directory) / 'invalid'
            shutil.copytree(groups[-1]['captures'][-1].parent, target)
            path = target / 'capture.json'
            metadata = json.loads(path.read_text())
            path.write_text(json.dumps({**metadata, 'raw_sha256': '0' * 64}))
            groups[-1]['captures'] = [path]
            before = {p: p.read_bytes() for p in target.iterdir()}
            for ordering in (groups, list(reversed(groups))):
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    audit_pk5_history(ordering)
                completed = self.cli(ordering)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, '')
                self.assertNotIn('Traceback', completed.stderr)
            self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)


    def test_calendar_only_dst_gaps_count_23_and_25_missing_periods_without_evidence(self):
        # These are calendar-only selections, not invented source captures or admission.
        for start, end, missing_hours in (
            (date(2026, 3, 28), date(2026, 3, 30), 23),
            (date(2026, 10, 24), date(2026, 10, 26), 25),
        ):
            with self.subTest(start=start):
                coverage = _calendar_coverage({start: True, end: True})
                self.assertEqual(coverage[str(missing_hours)], dict(
                    calendar_days=1, absent_days=1, observed_unselected_days=0,
                    selected_days=0, calendar_periods=missing_hours,
                    selected_periods=0, uncovered_periods=missing_hours))
                self.assertEqual(coverage['24'], dict(
                    calendar_days=2, absent_days=0, observed_unselected_days=0,
                    selected_days=2, calendar_periods=48,
                    selected_periods=48, uncovered_periods=0))
                other = '25' if missing_hours == 23 else '23'
                self.assertEqual(set(coverage[other].values()), {0})
                self.assertEqual(sum(v['calendar_periods'] for v in coverage.values()),
                                 48 + missing_hours)
                self.assertEqual(sum(v['uncovered_periods'] for v in coverage.values()),
                                 missing_hours)

    def test_single_day_without_selection_is_uncovered_not_absent(self):
        coverage = _calendar_coverage({date(2026, 11, 22): False})
        self.assertEqual(coverage['24'], dict(
            calendar_days=1, absent_days=0, observed_unselected_days=1,
            selected_days=0, calendar_periods=24, selected_periods=0, uncovered_periods=24))

    def test_group_errors_are_rejected_before_any_evidence_read(self):
        good = dict(day=date(2026, 11, 22), captures=[Path('must-not-be-opened.json')])
        cases = [[], None, [good, good], [dict(good, unexpected=True)],
                 [dict(good, captures=[])], [dict(good, captures='capture.json')]]
        cases += [[dict(good, day=value)] for value in
                  ('2026-11-22', datetime(2026, 11, 22), date.min, date.max, False)]
        for groups in cases:
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                audit_pk5_history(groups)


if __name__ == '__main__':
    unittest.main()
