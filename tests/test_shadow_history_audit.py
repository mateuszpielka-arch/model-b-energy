from copy import deepcopy
from datetime import date
from pathlib import Path
import unittest
from shadow_history_audit import audit_rows,read_csv
from shadow_scoring import expected_periods

class HistoryAuditTests(unittest.TestCase):
    def rows(self,day='2026-11-03'):
        return [dict(delivery_date=day,period_start_utc=p.isoformat(),fix1='100',jwcd_mw='10000',
            actual_verified='true',publication_ts=day+'T00:00:00Z',retrieved_at_utc=day+'T00:00:00Z',
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
