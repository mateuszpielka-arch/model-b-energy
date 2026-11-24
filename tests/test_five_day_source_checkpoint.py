"""Reproduce the five-day retained source cohort without filling absent history."""
import json
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_asof import cutoff_for_day
from shadow_source_history import load_history_collection
from shadow_walkforward_csv import evaluate_csv_rows

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/paired_source_audit_2026-11-25.json'


def evaluate(report):
    return evaluate_csv_rows(
        report['rows'], evaluated_at=datetime.fromisoformat('2026-11-24T10:15:51.127696+00:00'),
        target_from=date(2026, 11, 23), target_to=date(2026, 11, 25))


def summary(report):
    return dict(periods=report['periods'],
                days=[{key: entry[key] for key in (
                    'delivery_date', 'periods', 'cutoff_utc', 'selected_snapshot_id',
                    'pk5_captures', 'rejected_captures', 'fix1_evidence')}
                      for entry in report['days']],
                history_audit=report['history_audit'],
                walkforward=evaluate(report)['walkforward'])


class FiveDayCheckpointTests(unittest.TestCase):
    def test_new_day_at_each_position_reproduces_checkpoint_and_keeps_own_cutoffs(self):
        saved = json.loads(CHECKPOINT.read_text())
        self.assertEqual(saved['source_commit'], '22a9e382b8b0ea6995c68c8ea3eb48bb8d0157d3')
        groups = [[ROOT / p for p in group] for group in saved['source_groups']]
        self.assertEqual([len(g) - 1 for g in groups], [1, 1, 4, 6, 8])
        files = {p for g in groups for item in g for p in item.parent.iterdir() if p.is_file()}
        files.add(CHECKPOINT)
        before = {p: sha256(p.read_bytes()).hexdigest() for p in files}
        self.assertEqual(len(files), 56)
        checked = 0
        for prior in (groups[:-1], list(reversed(groups[:-1]))):
            for position in range(len(groups)):
                ordered = prior[:position] + [groups[-1]] + prior[position:]
                report = load_history_collection(ordered)
                self.assertEqual(summary(report), saved['report'])
                self.assertEqual(len({r['period_start_utc'] for r in report['rows']}), 120)
                checked += 1
        self.assertEqual(checked, 10)
        audit = report['history_audit']
        self.assertEqual((audit['complete_pair_days'], audit['model_ready_days'],
                          audit['longest_model_ready_run']), (5, 5, 3))
        for row in report['rows']:
            cutoff = cutoff_for_day(date.fromisoformat(row['delivery_date']))
            for key in ('publication_ts', 'retrieved_at', 'retrieval_completed_at'):
                self.assertLessEqual(datetime.fromisoformat(row[key]), cutoff)
            self.assertGreater(datetime.fromisoformat(row['actual_available_at']), cutoff)
        comparison = evaluate(report)['walkforward']
        self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
        self.assertEqual(comparison['ranking'], [])
        self.assertEqual(comparison['target_missing_ranges'], [
            dict(start_day='2026-11-23', end_day='2026-11-23', days=1)])
        self.assertEqual([s['day'] for s in comparison['skipped']], ['2026-11-24', '2026-11-25'])
        for skipped, admitted in zip(comparison['skipped'], (3, 4), strict=True):
            for window in (30, 60, 90):
                coverage = skipped['training_csv_coverage_by_window'][str(window) + 'D']
                self.assertEqual(coverage['admitted_by_csv_audit_days'], admitted)
                self.assertEqual(coverage['absent_from_csv_days'], window - admitted)
                self.assertEqual(coverage['admitted_with_fix1_after_target_cutoff_days'], 0)
        self.assertEqual({p: sha256(p.read_bytes()).hexdigest() for p in files}, before)

    def test_cli_preserves_prior_training_history_and_reports_missing_target_day(self):
        saved = json.loads(CHECKPOINT.read_text())
        groups = [[ROOT / p for p in g] for g in saved['source_groups']]
        expected = load_history_collection(groups)
        expected['evaluation'] = dict(
            evaluated_at_utc=saved['evaluated_at_utc'], **evaluate(expected))
        expected = json.loads(json.dumps(expected))
        for ordered in (groups, list(reversed(groups))):
            command = [sys.executable, str(ROOT / 'shadow_source_collection.py'),
                       '--format', 'json', '--walk-forward-at', '2026-11-24T11:15:51.127696+01:00',
                       '--target-from', '2026-11-23', '--target-to', '2026-11-25']
            for group in ordered:
                command += ['--day', *map(str, group)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(len(expected['rows']), 120)
        self.assertEqual(expected['evaluation']['walkforward']['target_coverage'], dict(
            calendar_days=3, absent_from_csv_days=1, excluded_by_csv_audit_days=0,
            admitted_but_unscored_days=2, scored_on_common_sample_days=0))


if __name__ == '__main__':
    unittest.main()
