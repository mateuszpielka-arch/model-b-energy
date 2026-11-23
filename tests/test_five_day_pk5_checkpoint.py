"""Reproduce the retained five-day PK5-only audit from explicit original sources."""
from datetime import date, datetime
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_pk5_history_audit import audit_pk5_history

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/pk5_history_audit_2026-11-25.json'


def load_case():
    checkpoint = json.loads(CHECKPOINT.read_text())
    groups = [dict(day=date.fromisoformat(g['day']),
                   captures=[ROOT / path for path in g['captures']])
              for g in checkpoint['source_groups']]
    return checkpoint, groups


class FiveDayPK5CheckpointTests(unittest.TestCase):
    def test_all_day_orders_reproduce_original_selections_and_explicit_missing_day(self):
        checkpoint, groups = load_case()
        files = {p: p.read_bytes() for group in groups for capture in group['captures']
                 for p in capture.parent.iterdir() if p.is_file()}
        self.assertEqual(checkpoint['source_commit'], '923a454e7d9ad4a875bf3edf6f8a2d97aedfac83')
        expected = checkpoint['pk5_history_audit']
        self.assertTrue(expected['read_only'])
        self.assertFalse(expected['model_readiness_assessed'])
        self.assertEqual((expected['observed_days'], expected['selected_days'],
                          expected['unselected_days'], expected['selected_periods']), (5, 5, 0, 120))
        self.assertEqual(sum(len(day['captures']) for day in expected['days']), 17)
        self.assertEqual(expected['missing_delivery_ranges'],
                         [dict(start_day='2026-11-23', end_day='2026-11-23', days=1)])
        self.assertEqual(expected['calendar_coverage_by_hours']['24'], dict(
            calendar_days=6, absent_days=1, observed_unselected_days=0,
            selected_days=5, calendar_periods=144, selected_periods=120, uncovered_periods=24))
        chosen_suffixes = {'2026-11-20': '_second', '2026-11-21': '_third',
                           '2026-11-22': '_fourth', '2026-11-24': '_sixth',
                           '2026-11-25': '_second'}
        for report in expected['days']:
            label = report['delivery_date']
            chosen = ROOT / ('tests/fixtures/pse_pk5_delivery_' + label +
                             chosen_suffixes[label]) / 'capture.json'
            metadata = json.loads(chosen.read_text())
            selected_capture, = [item for item in report['captures']
                                 if item['snapshot_id'] == report['selected_snapshot_id']]
            self.assertEqual(selected_capture['raw_sha256'], metadata['raw_sha256'])
            for field in ('retrieved_at_utc', 'retrieval_completed_at_utc'):
                self.assertEqual(datetime.fromisoformat(selected_capture[field]),
                                 datetime.fromisoformat(metadata[field]))
            if 'snapshot_id' in metadata:
                self.assertEqual(report['selected_snapshot_id'], metadata['snapshot_id'])
            cutoff = datetime.fromisoformat(report['cutoff_utc'])
            self.assertEqual(len(report['selected_rows']), 24)
            for capture in report['captures']:
                started = datetime.fromisoformat(capture['retrieved_at_utc'])
                completed = datetime.fromisoformat(capture['retrieval_completed_at_utc'])
                self.assertLessEqual(started, completed)
                self.assertLessEqual(completed, cutoff)
                for stamp in capture['publication_counts_utc']:
                    self.assertLessEqual(datetime.fromisoformat(stamp), completed)
        for ordering in permutations(groups):
            self.assertEqual(audit_pk5_history(ordering), expected)
        reversed_captures = [dict(day=g['day'], captures=list(reversed(g['captures'])))
                             for g in groups]
        self.assertEqual(audit_pk5_history(list(reversed(reversed_captures))), expected)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_cli_in_both_orders_matches_checkpoint_without_modifying_evidence(self):
        checkpoint, groups = load_case()
        files = {p: p.read_bytes() for group in groups for capture in group['captures']
                 for p in capture.parent.iterdir() if p.is_file()}
        files[CHECKPOINT] = CHECKPOINT.read_bytes()
        for ordering in (groups, list(reversed(groups))):
            command = [sys.executable, str(ROOT / 'shadow_pk5_history_audit.py'),
                       '--require-selection']
            for group in ordering:
                command += ['--source-day', group['day'].isoformat(), *map(str, group['captures'])]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), checkpoint['pk5_history_audit'])
        self.assertEqual({p: p.read_bytes() for p in files}, files)


if __name__ == '__main__':
    unittest.main()
