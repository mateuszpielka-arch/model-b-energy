"""Synthetic complete windows exercise the strict exporter and real model engine.

Only the local evidence loader is substituted; these are not retained TGE/PSE
observations or a production forecast. Model fitting and scoring are not mocked.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import io
import json
import unittest
from unittest.mock import patch

from shadow_asof import cutoff_for_day
from shadow_source_collection import main
import test_shadow_ols as fixtures


def synthetic_collection(target):
    args = fixtures.OLSTests().data(n=90, target=target)
    snapshots = [packet['snapshots'][0] for packet in args['history']] + args['target_snapshots']
    rows = []
    for snapshot in snapshots:
        day = snapshot['delivery_date']
        for row in snapshot['rows']:
            rows.append(dict(
                delivery_date=day.isoformat(), hour='',
                period_start_utc=row['period_start_utc'].isoformat(),
                fix1=str(2 * row['jwcd_mw'] + 10), jwcd_mw=str(row['jwcd_mw']),
                actual_verified='true', actual_version='synthetic:' + day.isoformat(),
                actual_available_at=(snapshot['retrieved_at'] + timedelta(hours=3)).isoformat(),
                jwcd_snapshot_id=snapshot['id'], publication_ts=row['publication_ts'].isoformat(),
                retrieved_at=snapshot['retrieved_at'].isoformat(),
                retrieval_completed_at=snapshot['retrieval_completed_at'].isoformat()))
    return dict(rows=rows, periods=len(rows),
                days=[dict(delivery_date=s['delivery_date'].isoformat()) for s in snapshots])


def export(collection, target, strict=True):
    argv = ['shadow_source_collection.py', '--day', 'synthetic-manifest', 'synthetic-pk5',
            '--format', 'json', '--walk-forward-at',
            (cutoff_for_day(target) + timedelta(days=2)).isoformat(),
            '--target-from', target.isoformat(), '--target-to', target.isoformat()]
    if strict:
        argv.append('--require-common-sample')
    stream = io.StringIO()
    status = 0
    with patch('sys.argv', argv), patch(
        'shadow_source_collection.load_history_collection', return_value=deepcopy(collection),
    ) as loader, redirect_stdout(stream):
        try:
            main()
        except SystemExit as exc:
            status = exc.code
        loader.assert_called_once()
    return status, json.loads(stream.getvalue())


class PositiveCommonSampleTests(unittest.TestCase):
    def test_complete_synthetic_23_24_25_hour_targets_pass_with_six_models_on_same_periods(self):
        for target, hours in ((date(2026, 3, 29), 23), (date(2026, 11, 25), 24),
                              (date(2026, 10, 25), 25)):
            with self.subTest(target=target):
                collection = synthetic_collection(target)
                before = deepcopy(collection)
                status, result = export(collection, target)
                normal_status, normal = export(collection, target, strict=False)
                self.assertEqual((status, normal_status), (0, 0))
                self.assertEqual(result, normal)
                self.assertEqual(result['evaluation']['csv_admission']['admitted_days'], 91)
                comparison = result['evaluation']['walkforward']
                self.assertEqual((comparison['common_days'], comparison['common_periods']), (1, hours))
                self.assertEqual(comparison['skipped'], [])
                self.assertEqual(len(comparison['ranking']), 6)
                self.assertEqual({r['model'] for r in comparison['ranking']},
                                 {'ols30', 'ols60', 'ols90', 'similar_shape', 'similar_level', 'median4'})
                self.assertEqual({(r['days'], r['periods']) for r in comparison['ranking']}, {(1, hours)})
                for entry in comparison['ranking']:
                    if entry['model'].startswith('ols'):
                        self.assertLess(abs(Decimal(entry['mae'])), Decimal('1e-24'))
                self.assertEqual(collection, before)

    def test_training_actual_one_microsecond_after_target_cutoff_blocks_strict_success(self):
        target = date(2026, 10, 25)
        collection = synthetic_collection(target)
        prior = (target - timedelta(days=1)).isoformat()
        late = (cutoff_for_day(target) + timedelta(microseconds=1)).isoformat()
        for row in collection['rows']:
            if row['delivery_date'] == prior:
                row['actual_available_at'] = late
        before = deepcopy(collection)
        status, result = export(collection, target)
        normal_status, normal = export(collection, target, strict=False)
        self.assertEqual((status, normal_status), (3, 0))
        self.assertEqual(result, normal)
        self.assertEqual(result['evaluation']['csv_admission']['admitted_days'], 91)
        comparison = result['evaluation']['walkforward']
        self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
        self.assertEqual(comparison['ranking'], [])
        skipped, = comparison['skipped']
        self.assertEqual(skipped['stage'], 'ols30')
        self.assertEqual(skipped['late_training_actual_days'], [prior])
        for window in (30, 60, 90):
            coverage = skipped['training_csv_coverage_by_window'][str(window) + 'D']
            self.assertEqual(coverage['admitted_by_csv_audit_days'], window)
            self.assertEqual(coverage['admitted_with_fix1_after_target_cutoff_days'], 1)
            self.assertEqual(coverage['fix1_available_after_target_cutoff_utc_by_day'], {prior: late})
        self.assertEqual(collection, before)


if __name__ == '__main__':
    unittest.main()
