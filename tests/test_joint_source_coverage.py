"""Reproduce retained joint evidence coverage without admitting model training."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import unittest

from shadow_source_coverage import audit_source_coverage

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/source_coverage_2026-11-25.json'


def load_case():
    checkpoint = json.loads(CHECKPOINT.read_text())
    groups = [dict(day=date.fromisoformat(g['day']), captures=[ROOT / p for p in g['captures']])
              for g in checkpoint['source_groups']]
    manifests = [ROOT / p for p in checkpoint['fix1_manifests']]
    return checkpoint, groups, manifests


def source_bytes(groups, manifests):
    directories = {p.parent for g in groups for p in g['captures']}
    directories.update(p.parent for p in manifests)
    return {p: p.read_bytes() for directory in directories
            for p in directory.iterdir() if p.is_file()}


class JointSourceCoverageTests(unittest.TestCase):
    def test_real_sources_reproduce_joint_checkpoint_in_both_orders(self):
        checkpoint, groups, manifests = load_case()
        files = source_bytes(groups, manifests)
        self.assertEqual(checkpoint['source_commit'], '00055ecee81723c6890fe7fdb0c04a0e9c44c0d1')
        expected = checkpoint['audit']
        self.assertTrue(expected['read_only'])
        self.assertFalse(expected['model_readiness_assessed'])
        coverage = expected['source_coverage']
        self.assertEqual(coverage['both_source_days'],
                         ['2026-11-20', '2026-11-21', '2026-11-22', '2026-11-24'])
        self.assertEqual(coverage['pk5_only_days'], ['2026-11-25'])
        self.assertEqual(coverage['fix1_only_days'], ['2026-11-23'])
        self.assertEqual(coverage['neither_source_days'], [])
        self.assertEqual(coverage['fix1_price_conflict_days'], [])
        self.assertEqual(coverage['by_hours']['24'], dict(
            calendar_days=6, calendar_periods=144, both_source_days=4, both_source_periods=96,
            pk5_only_days=1, pk5_only_periods=24, fix1_only_days=1, fix1_only_periods=24,
            neither_source_days=0, neither_source_periods=0))
        self.assertEqual(sum(len(day['captures']) for day in expected['pk5_audit']['days']), 21)
        self.assertEqual(expected['fix1_audit']['distinct_captures'], 7)
        self.assertEqual(expected['fix1_audit']['delivery_days'], 5)
        for ordered_groups, ordered_manifests in (
            (groups, manifests),
            ([dict(day=g['day'], captures=list(reversed(g['captures'])))
              for g in reversed(groups)], list(reversed(manifests))),
        ):
            report = audit_source_coverage(
                pk5_groups=ordered_groups, fix1_manifests=ordered_manifests,
                start_day=date(2026, 11, 20), end_day=date(2026, 11, 25))
            self.assertEqual(report, expected)
        self.assertEqual(source_bytes(groups, manifests), files)

    def test_explicit_scope_counts_absence_without_dropping_validated_source_reports(self):
        _, groups, manifests = load_case()
        files = source_bytes(groups, manifests)
        expanded = audit_source_coverage(pk5_groups=groups, fix1_manifests=manifests,
                                         start_day=date(2026, 11, 19), end_day=date(2026, 11, 26))
        coverage = expanded['source_coverage']
        self.assertEqual(coverage['neither_source_days'], ['2026-11-19', '2026-11-26'])
        self.assertEqual(coverage['calendar_days'], 8)
        self.assertEqual(coverage['by_hours']['24']['calendar_periods'], 192)
        self.assertEqual(coverage['by_hours']['24']['both_source_periods'], 96)
        self.assertEqual(coverage['by_hours']['24']['neither_source_periods'], 48)
        restricted = audit_source_coverage(pk5_groups=groups, fix1_manifests=manifests,
                                           start_day=date(2026, 11, 21), end_day=date(2026, 11, 24))
        self.assertEqual(restricted['source_coverage']['both_source_days'],
                         ['2026-11-21', '2026-11-22', '2026-11-24'])
        self.assertEqual(restricted['source_coverage']['fix1_only_days'], ['2026-11-23'])
        self.assertEqual(restricted['source_coverage']['pk5_only_days'], [])
        self.assertEqual(restricted['source_coverage']['by_hours']['24']['calendar_periods'], 96)
        self.assertEqual(restricted['pk5_audit']['observed_days'], 5)
        self.assertEqual(restricted['fix1_audit']['delivery_days'], 5)
        self.assertEqual(source_bytes(groups, manifests), files)

    def test_existing_clis_reproduce_both_source_audits_without_modifying_evidence(self):
        checkpoint, groups, manifests = load_case()
        files = source_bytes(groups, manifests)
        files[CHECKPOINT] = CHECKPOINT.read_bytes()
        for reverse in (False, True):
            ordered_groups = list(reversed(groups)) if reverse else groups
            ordered_manifests = list(reversed(manifests)) if reverse else manifests
            pk5 = [sys.executable, str(ROOT / 'shadow_pk5_history_audit.py'), '--require-selection']
            for group in ordered_groups:
                pk5 += ['--source-day', group['day'].isoformat(), *map(str, group['captures'])]
            fix1 = [sys.executable, str(ROOT / 'shadow_manifest_audit.py'),
                    '--start-day', '2026-11-20', '--end-day', '2026-11-25',
                    '--fail-on-conflict', *map(str, ordered_manifests)]
            for command, key in ((pk5, 'pk5_audit'), (fix1, 'fix1_audit')):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(json.loads(result.stdout), checkpoint['audit'][key])
        self.assertEqual(source_bytes(groups, manifests), {p: b for p, b in files.items() if p != CHECKPOINT})
        self.assertEqual(CHECKPOINT.read_bytes(), files[CHECKPOINT])


if __name__ == '__main__':
    unittest.main()
