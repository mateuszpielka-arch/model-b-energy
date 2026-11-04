from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import unittest
from shadow_history_audit import audit_rows,read_csv
from shadow_scoring import expected_periods

class HistoryAuditTests(unittest.TestCase):
    def rows(self,day='2026-11-03'):
        return [dict(delivery_date=day,period_start_utc=p.isoformat(),fix1='100',jwcd_mw='10000',
            actual_verified='true',actual_version='v1',jwcd_snapshot_id=day,actual_available_at=day+'T00:00:00Z',publication_ts=day+'T00:00:00Z',retrieved_at_utc=day+'T00:00:00Z',
            retrieval_completed_at=day+'T00:00:00Z') for p in expected_periods(date.fromisoformat(day))]

    def test_real_reference_days_are_not_training_ready(self):
        f=Path(__file__).parent/'fixtures'; rows=[]
        for day in ('2026-11-02','2026-11-03'): rows+=read_csv(f/f'tge_fix1_{day}_reference.csv')
        out=audit_rows(rows)
        self.assertEqual(out['model_ready_days'],0)
        self.assertEqual([d['valid_fix1_rows'] for d in out['days']],[24,24])
        self.assertTrue(all(d['complete_periods'] for d in out['days']))
        self.assertTrue(all('MISSING_OR_INVALID_JWCD' in d['issues'] for d in out['days']))

    def test_late_availability_rejected(self):
        out=audit_rows(self.rows());self.assertEqual(out['model_ready_days'],0)
        self.assertIn('AVAILABILITY_NOT_PROVEN',out['days'][0]['issues'])

    def test_valid_synthetic_day_and_no_mutation(self):
        rows=self.rows()
        for r in rows:
            r.update(publication_ts='2026-11-02T06:00:00Z',retrieved_at_utc='2026-11-02T07:00:00Z',retrieval_completed_at='2026-11-02T07:01:00Z')
        before=deepcopy(rows);self.assertEqual(audit_rows(rows)['model_ready_days'],1);self.assertEqual(rows,before)

    def test_duplicate_hour_not_complete(self):
        rows=self.rows(); rows[-1]=deepcopy(rows[0])
        self.assertFalse(audit_rows(rows)['days'][0]['complete_periods'])

    def test_dst_requires_utc_and_counts_25(self):
        rows=self.rows('2026-10-25');self.assertTrue(audit_rows(rows)['days'][0]['complete_periods'])
        for i,r in enumerate(rows): r.pop('period_start_utc');r['hour']=str(i+1)
        self.assertFalse(audit_rows(rows)['days'][0]['complete_periods'])

    def test_invalid_dates_and_nan(self):
        rows=self.rows();rows[0]['fix1']='NaN';rows.append({'delivery_date':'bad'})
        out=audit_rows(rows);self.assertEqual(out['invalid_date_rows'],1)
        self.assertEqual(out['days'][0]['valid_fix1_rows'],23)

    def test_comparison_coverage_requires_consecutive_ready_days(self):
        first = date(2026, 6, 1)
        rows = []
        for offset in (*range(30), 31):
            day = first + timedelta(days=offset)
            stamp = (day - timedelta(days=1)).isoformat() + 'T06:00:00Z'
            for row in self.rows(day.isoformat()):
                row.update(publication_ts=stamp, retrieved_at_utc=stamp,
                           retrieval_completed_at=stamp)
                rows.append(row)
        out = audit_rows(reversed(rows))
        self.assertEqual(out['model_ready_days'], 31)
        self.assertEqual(out['longest_model_ready_run'], 30)
        self.assertEqual(out['missing_days_for_first_comparison'],
                         {'30D': 1, '60D': 31, '90D': 61})

    def test_distinguishes_missing_invalid_and_late_availability(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        del rows[0]['retrieval_completed_at']
        rows[1]['publication_ts'] = '2026-11-02T08:00:00'
        rows[2]['retrieval_completed_at'] = '2026-11-02T08:50:01Z'
        rows[3]['publication_ts'] = '2026-11-02T08:51:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 20)
        self.assertEqual(day['availability_missing_timestamp_rows'], 1)
        self.assertEqual(day['availability_invalid_timestamp_rows'], 2)
        self.assertEqual(day['availability_after_cutoff_rows'], 1)
        self.assertFalse(day['model_ready'])

    def test_accepts_canonical_retrieved_at_and_rejects_conflicting_legacy_alias(self):
        rows = self.rows()
        for row in rows:
            del row['retrieved_at_utc']
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at='2026-11-02T09:45:00+01:00',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        rows[0]['retrieved_at_utc'] = '2026-11-02T08:45:00Z'
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['retrieved_at_utc'] = '2026-11-02T08:44:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_invalid_timestamp_rows'], 1)
        self.assertFalse(day['model_ready'])


    def test_fix1_requires_timestamp_strictly_after_issuance(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['actual_available_at'] = '2026-11-02T09:50:00+01:00'
        del rows[1]['actual_available_at']
        day, = audit_rows(rows)['days']
        self.assertEqual(day['actual_after_issuance_rows'], 22)
        self.assertIn('ACTUAL_AVAILABILITY_NOT_PROVEN', day['issues'])
        self.assertFalse(day['model_ready'])


    def test_missing_or_mixed_fix1_versions_block_coverage(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:50:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['actual_version'] = 'v2'
        del rows[1]['actual_version']
        day, = audit_rows(rows)['days']
        self.assertFalse(day['model_ready'])
        self.assertEqual(day['issues'], [
            'CONFLICTING_ACTUAL_VERSIONS', 'MISSING_ACTUAL_VERSION',
        ])

    def test_first_candidate_requires_training_prices_available_by_target_cutoff(self):
        first = date(2026, 6, 1)
        rows = []
        for offset in range(32):
            day = first + timedelta(days=offset)
            prior = (day - timedelta(days=1)).isoformat()
            for row in self.rows(day.isoformat()):
                stamp = prior + 'T06:00:00Z'
                row.update(publication_ts=stamp, retrieved_at_utc=stamp,
                           retrieval_completed_at=stamp,
                           actual_available_at=prior + 'T11:00:00Z')
                rows.append(row)
        result = audit_rows(rows)
        self.assertEqual(result['first_comparison_candidate']['30D'], '2026-07-01')
        self.assertEqual(result['comparison_candidates'],
                         {'30D': ['2026-07-01', '2026-07-02'], '60D': [], '90D': []})
        rows[0]['actual_available_at'] = '2026-06-30T07:50:01Z'
        result = audit_rows(rows)
        self.assertEqual(result['longest_model_ready_run'], 32)
        self.assertEqual(result['first_comparison_candidate']['30D'], '2026-07-02')
        self.assertEqual(result['comparison_candidates']['30D'], ['2026-07-02'])

    def test_mixed_jwcd_retrieval_windows_block_ready_day(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:49:00Z')
        self.assertTrue(audit_rows(rows)['days'][0]['model_ready'])
        rows[0]['retrieval_completed_at'] = '2026-11-02T08:50:00Z'
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 24)
        self.assertEqual(day['issues'], ['MIXED_JWCD_RETRIEVAL_WINDOWS'])
        self.assertFalse(day['model_ready'])

    def test_missing_or_mixed_snapshot_ids_block_ready_day(self):
        rows = self.rows()
        for row in rows:
            row.update(publication_ts='2026-11-02T08:00:00Z',
                       retrieved_at_utc='2026-11-02T08:45:00Z',
                       retrieval_completed_at='2026-11-02T08:49:00Z')
        rows[0]['jwcd_snapshot_id'] = 'different-snapshot'
        del rows[1]['jwcd_snapshot_id']
        day, = audit_rows(rows)['days']
        self.assertEqual(day['availability_proven_rows'], 24)
        self.assertEqual(day['issues'], [
            'MISSING_JWCD_SNAPSHOT_ID', 'MIXED_JWCD_SNAPSHOT_IDS',
        ])
        self.assertFalse(day['model_ready'])
