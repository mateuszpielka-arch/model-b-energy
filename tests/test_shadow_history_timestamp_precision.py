from copy import deepcopy
from datetime import date, timedelta
import unittest

from shadow_asof import cutoff_for_day
from shadow_history_audit import _stamp, audit_rows
from shadow_scoring import expected_periods
from shadow_walkforward_csv import evaluate_csv_rows


class HistoryTimestampPrecisionTests(unittest.TestCase):
    def rows(self, day):
        cutoff = cutoff_for_day(day)
        return [
            dict(
                delivery_date=day.isoformat(), period_start_utc=period.isoformat(),
                jwcd_mw='10000', fix1_pln_mwh='500', actual_verified='true',
                actual_version='synthetic-actual', jwcd_snapshot_id='synthetic-pk5',
                publication_ts=cutoff.isoformat(timespec='microseconds'),
                retrieved_at=cutoff.isoformat(timespec='microseconds'),
                retrieval_completed_at=cutoff.isoformat(timespec='microseconds'),
                actual_available_at=(cutoff + timedelta(hours=1)).isoformat(timespec='microseconds'),
            )
            for period in expected_periods(day)
        ]

    def test_submicrosecond_jwcd_time_is_invalid_on_23_24_25_hour_days(self):
        for day, hours in ((date(2026, 3, 29), 23), (date(2026, 11, 20), 24),
                           (date(2026, 10, 25), 25)):
            rows = self.rows(day)
            self.assertEqual(len(rows), hours)
            self.assertEqual(audit_rows(rows)['model_ready_days'], 1)
            for field in ('publication_ts', 'retrieved_at', 'retrieval_completed_at'):
                with self.subTest(day=day, field=field):
                    changed = deepcopy(rows)
                    changed[0][field] = changed[0][field].replace('.000000', '.0000001')
                    before = deepcopy(changed)
                    report = audit_rows(changed)
                    entry, = report['days']
                    self.assertFalse(entry['model_ready'])
                    self.assertEqual(entry['invalid_jwcd_timestamp_rows_by_field'][field], 1)
                    self.assertEqual(entry['invalid_jwcd_timestamp_periods_utc_by_field'][field],
                                     [rows[0]['period_start_utc']])
                    comparison = evaluate_csv_rows(
                        changed, evaluated_at=cutoff_for_day(day) + timedelta(hours=1),
                        target_from=day, target_to=day,
                    )
                    self.assertEqual(comparison['csv_admission']['admitted_days'], 0)
                    self.assertIn(day.isoformat(),
                                  comparison['walkforward']['targets_excluded_by_csv_audit'])
                    self.assertEqual(comparison['walkforward']['ranking'], [])
                    self.assertEqual(changed, before)

    def test_actual_and_period_times_cannot_be_truncated(self):
        for day in (date(2026, 3, 29), date(2026, 11, 20), date(2026, 10, 25)):
            rows = self.rows(day)
            changed = deepcopy(rows)
            changed[0]['actual_available_at'] = changed[0]['actual_available_at'].replace(
                '.000000', '.0000001')
            with self.subTest(day=day, field='actual_available_at'):
                entry, = audit_rows(changed)['days']
                self.assertFalse(entry['model_ready'])
                self.assertEqual(entry['actual_availability_invalid_timestamp_rows'], 1)
                self.assertEqual(entry['actual_availability_invalid_timestamp_periods_utc'],
                                 [rows[0]['period_start_utc']])
            changed = deepcopy(rows)
            period = _stamp(changed[0]['period_start_utc'])
            changed[0]['period_start_utc'] = period.isoformat(timespec='microseconds').replace(
                '.000000', '.0000001')
            with self.subTest(day=day, field='period_start_utc'):
                entry, = audit_rows(changed)['days']
                self.assertFalse(entry['model_ready'])
                self.assertIn('INVALID_OR_AMBIGUOUS_PERIOD', entry['issues'])
                self.assertEqual(entry['missing_periods_utc'], [rows[0]['period_start_utc']])

    def test_stamp_requires_full_aware_text_and_preserves_supported_instants(self):
        cutoff = cutoff_for_day(date(2026, 11, 20))
        for value in (cutoff, '2026-11-19T08:50:00Z',
                      '2026-11-19 08:50:00.000000+00:00',
                      '2026-11-19T09:50:00.000000+01:00'):
            with self.subTest(valid=value):
                self.assertEqual(_stamp(value), cutoff)
        for value in ('2026-11-19T08:50+00:00', '2026-11-19T08:50:00',
                      '2026-11-19T08:50:00.0000001Z', None, 42):
            with self.subTest(invalid=value), self.assertRaises(ValueError):
                _stamp(value)
