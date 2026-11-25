"""Reproduce the explicit eight-day source coverage cohort without model admission."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_source_coverage import audit_source_coverage

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/source_coverage_2026-11-27.json'


def case():
    saved = json.loads(CHECKPOINT.read_text())
    groups = [dict(day=date.fromisoformat(g['day']), captures=[ROOT / p for p in g['captures']])
              for g in saved['source_groups']]
    manifests = [ROOT / p for p in saved['fix1_manifests']]
    return saved, groups, manifests


def fingerprints(groups, manifests):
    files = {p for source in [*[p for g in groups for p in g['captures']], *manifests]
             for p in source.parent.iterdir() if p.is_file()}
    files.add(CHECKPOINT)
    return {p: sha256(p.read_bytes()).hexdigest() for p in files}


class November27CoverageTests(unittest.TestCase):
    def test_real_cohort_reproduces_checkpoint_and_source_only_gaps_in_both_orders(self):
        saved, groups, manifests = case()
        before = fingerprints(groups, manifests)
        self.assertEqual(len(before), 112)
        self.assertEqual(saved['source_commit'], '9212c1850b56ed82dcf6efd743b5c5ef39dfcecb')
        self.assertEqual([len(g['captures']) for g in groups], [2, 3, 4, 6, 8, 12, 4])
        expected = saved['audit']
        for ordered_groups, ordered_manifests in (
            (groups, manifests),
            ([dict(day=g['day'], captures=list(reversed(g['captures']))) for g in reversed(groups)],
             list(reversed(manifests))),
        ):
            report = audit_source_coverage(
                pk5_groups=ordered_groups, fix1_manifests=ordered_manifests,
                start_day=date(2026, 11, 20), end_day=date(2026, 11, 27))
            self.assertEqual(report, expected)
        self.assertTrue(report['read_only'])
        self.assertFalse(report['model_readiness_assessed'])
        coverage = report['source_coverage']
        self.assertEqual(coverage['both_source_days'],
                         ['2026-11-20', '2026-11-21', '2026-11-22', '2026-11-24', '2026-11-25', '2026-11-26'])
        self.assertEqual(coverage['pk5_only_days'], ['2026-11-27'])
        self.assertEqual(coverage['fix1_only_days'], ['2026-11-23'])
        self.assertEqual(coverage['neither_source_days'], [])
        self.assertEqual(coverage['fix1_price_conflict_days'], [])
        self.assertEqual(coverage['by_hours']['24'], dict(
            calendar_days=8, calendar_periods=192, both_source_days=6, both_source_periods=144,
            pk5_only_days=1, pk5_only_periods=24, fix1_only_days=1, fix1_only_periods=24,
            neither_source_days=0, neither_source_periods=0))
        for hours in ('23', '25'):
            self.assertEqual(set(coverage['by_hours'][hours].values()), {0})
        self.assertEqual(report['pk5_audit']['selected_days'], 7)
        self.assertEqual(report['pk5_audit']['selected_periods'], 168)
        self.assertEqual(sum(len(d['captures']) for d in report['pk5_audit']['days']), 39)
        last = report['pk5_audit']['days'][-1]
        self.assertEqual(last['cutoff_utc'], '2026-11-26T08:50:00+00:00')
        self.assertEqual(last['selected_snapshot_id'],
                         'pk5-capture-sha256:22de18bb9b5158a9453f1e1989a39c1e61745de8630c38b4d22dbe76ffbed897')
        self.assertEqual(report['fix1_audit']['delivery_days'], 7)
        self.assertEqual(report['fix1_audit']['distinct_captures'], 11)
        self.assertEqual(report['fix1_audit']['days_with_price_conflicts'], 0)
        self.assertEqual(fingerprints(groups, manifests), before)

    def test_strict_joint_cli_reproduces_complete_report_in_both_orders_without_writes(self):
        saved, groups, manifests = case()
        before = fingerprints(groups, manifests)
        for reverse in (False, True):
            ordered_groups = list(reversed(groups)) if reverse else groups
            ordered_manifests = list(reversed(manifests)) if reverse else manifests
            command = [sys.executable, str(ROOT / 'shadow_source_coverage.py'),
                       '--start-day', saved['start_day'], '--end-day', saved['end_day'],
                       '--require-selection', '--fail-on-conflict']
            for group in ordered_groups:
                paths = list(reversed(group['captures'])) if reverse else group['captures']
                command += ['--source-day', group['day'].isoformat(), *map(str, paths)]
            for manifest in ordered_manifests:
                command += ['--fix1-manifest', str(manifest)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout), saved['audit'])
        self.assertEqual(fingerprints(groups, manifests), before)


if __name__ == '__main__':
    unittest.main()
