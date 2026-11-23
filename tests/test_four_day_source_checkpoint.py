import itertools
import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_source_history import load_history_day, prepare_history_collection, load_history_collection
from shadow_walkforward_csv import evaluate_csv_rows


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/paired_source_audit_2026-11-24.json'


def source_groups():
    base = Path('tests/fixtures')
    return [
        [str(base / 'tge_delivery_2026-11-20/manifest.json'),
         str(base / 'pse_pk5_delivery_2026-11-20_second/capture.json')],
        [str(base / 'tge_delivery_2026-11-21/manifest.json'),
         str(base / 'pse_pk5_delivery_2026-11-21_third/capture.json')],
        [str(base / 'tge_delivery_2026-11-22/manifest.json'),
         *[str(base / ('pse_pk5_delivery_2026-11-22' + suffix) / 'capture.json')
           for suffix in ('', '_second', '_third', '_fourth')]],
        [str(base / 'tge_delivery_2026-11-24/manifest.json'),
         *[str(base / ('pse_pk5_delivery_2026-11-24' + suffix) / 'capture.json')
           for suffix in ('', '_second', '_third', '_fourth', '_fifth', '_sixth')]],
    ]


def prepare(groups):
    return prepare_history_collection(days=[
        load_history_day(ROOT / group[0], [ROOT / path for path in group[1:]])
        for group in groups
    ])


def evaluate(report):
    return evaluate_csv_rows(
        report['rows'], evaluated_at=datetime.fromisoformat('2026-11-23T10:14:34.948376+00:00'),
        target_from=date(2026, 11, 23), target_to=date(2026, 11, 24),
    )


def retained_summary(report):
    return dict(
        periods=report['periods'],
        days=[{key: entry[key] for key in (
            'delivery_date', 'periods', 'cutoff_utc', 'selected_snapshot_id',
            'pk5_captures', 'rejected_captures', 'fix1_evidence',
        )} for entry in report['days']],
        history_audit=report['history_audit'],
        walkforward=evaluate(report)['walkforward'],
    )


class FourDayCheckpointTests(unittest.TestCase):
    def test_retained_checkpoint_reproduces_in_all_day_orders_without_source_changes(self):
        saved = json.loads(CHECKPOINT.read_text())
        self.assertEqual(saved['source_groups'], source_groups())
        files = {path for group in source_groups() for item in group
                 for path in (ROOT / item).parent.iterdir() if path.is_file()}
        before = {path: sha256(path.read_bytes()).hexdigest() for path in files}
        for groups in itertools.permutations(source_groups()):
            report = prepare(groups)
            streamed = load_history_collection([[ROOT / path for path in group] for group in groups])
            self.assertEqual(streamed, report)
            self.assertEqual(retained_summary(report), saved['report'])
            self.assertEqual(len({row['period_start_utc'] for row in report['rows']}), 96)
        audit = report['history_audit']
        self.assertEqual((audit['complete_pair_days'], audit['model_ready_days'],
                          audit['longest_model_ready_run']), (4, 4, 3))
        self.assertEqual(audit['missing_days_for_first_comparison'],
                         {'30D': 28, '60D': 58, '90D': 88})
        comparison = evaluate(report)['walkforward']
        self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
        self.assertEqual(comparison['ranking'], [])
        self.assertEqual(comparison['target_missing_ranges'], [
            dict(start_day='2026-11-23', end_day='2026-11-23', days=1),
        ])
        skipped, = comparison['skipped']
        self.assertEqual(skipped['day'], '2026-11-24')
        for window in (30, 60, 90):
            coverage = skipped['training_csv_coverage_by_window'][str(window) + 'D']
            self.assertEqual(coverage['admitted_by_csv_audit_days'], 3)
            self.assertEqual(coverage['absent_from_csv_days'], window - 3)
            self.assertEqual(coverage['admitted_with_fix1_after_target_cutoff_days'], 0)
        self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in files}, before)


    def test_four_day_cli_reports_missing_paired_day_without_dropping_history(self):
        expected = prepare(source_groups())
        expected['evaluation'] = dict(
            evaluated_at_utc='2026-11-23T10:14:34.948376+00:00', **evaluate(expected))
        expected = json.loads(json.dumps(expected))
        for groups in (source_groups(), list(reversed(source_groups()))):
            command = [sys.executable, str(ROOT / 'shadow_source_collection.py'),
                       '--format', 'json', '--walk-forward-at', '2026-11-23T11:14:34.948376+01:00',
                       '--target-from', '2026-11-23', '--target-to', '2026-11-24']
            for group in groups:
                command.extend(['--day', *[str(ROOT / path) for path in group]])
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, '')
            self.assertEqual(json.loads(completed.stdout), expected)
            self.assertEqual(len(expected['rows']), 96)
            self.assertEqual(expected['evaluation']['walkforward']['target_coverage'],
                             dict(calendar_days=2, absent_from_csv_days=1,
                                  excluded_by_csv_audit_days=0, admitted_but_unscored_days=1,
                                  scored_on_common_sample_days=0))


if __name__ == '__main__':
    unittest.main()
