from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from shadow_scoring import expected_periods, score_day

class ScoringTests(unittest.TestCase):
    def args(self, day=date(2026,11,2)):
        periods = expected_periods(day)
        prior = datetime.combine(day-timedelta(days=1), datetime.min.time(), timezone.utc)
        return dict(day=day, forecast=[(p,'100') for p in periods],
                    actual=[(p,'100') for p in periods], forecast_id='41',
                    model_version='jwcd-shadow-30d', actual_version='verified-sha256-batch',
                    forecast_status='SHADOW', actual_verified=True,
                    publication_ts=prior+timedelta(hours=6),
                    retrieved_at=prior+timedelta(hours=7), issued_at=prior+timedelta(hours=7,minutes=30))

    def test_hand_calculated_zero_negative_positive(self):
        kw=self.args(); periods=expected_periods(kw['day'])
        kw['actual']=[(p,v) for p,v in zip(periods, ['0','-100','100']*8)]
        kw['forecast']=[(p,v) for p,v in zip(periods, ['10','-80','90']*8)]
        s=score_day(**kw)
        self.assertAlmostEqual(float(s.mae), 40/3)
        self.assertAlmostEqual(float(s.bias), 20/3)
        self.assertEqual((s.mape_pct,s.wape_pct), (Decimal(15),Decimal(20)))
        self.assertEqual((s.mape_periods,s.zero_actual_periods), (16,8))

    def test_all_zero_actual(self):
        kw=self.args(); kw['actual']=[(p,0) for p,_ in kw['actual']]
        s=score_day(**kw)
        self.assertIsNone(s.mape_pct); self.assertIsNone(s.wape_pct)
        self.assertEqual(s.mae,100); self.assertEqual(s.bias,100)

    def test_complete_dst_days(self):
        for day,n in [(date(2026,3,29),23),(date(2026,10,25),25),(date(2026,11,2),24)]:
            with self.subTest(day=day):
                s=score_day(**self.args(day)); self.assertEqual(s.periods,n); self.assertEqual(s.mae,0)

    def test_missing_and_wrong_periods_rejected(self):
        for field in ('forecast','actual'):
            kw=self.args(); kw[field]=kw[field][:-1]
            with self.subTest(field=field), self.assertRaises(ValueError): score_day(**kw)
        kw=self.args(); p,v=kw['actual'][0]; kw['actual'][0]=(p-timedelta(days=1),v)
        with self.assertRaises(ValueError): score_day(**kw)

    def test_duplicate_rejected(self):
        kw=self.args(); kw['actual'].append(kw['actual'][0])
        with self.assertRaises(ValueError): score_day(**kw)

    def test_nonfinite_and_invalid_prices_rejected(self):
        for value in ('NaN','Infinity','-Infinity',None,True,'bad'):
            kw=self.args(); p,_=kw['actual'][0]; kw['actual'][0]=(p,value)
            with self.subTest(value=value), self.assertRaises(ValueError): score_day(**kw)

    def test_unverified_and_missing_identity_rejected(self):
        for field,value in [('actual_verified',False),('actual_version',''),('forecast_id',''),('model_version',''),('forecast_status','DRAFT')]:
            kw=self.args(); kw[field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): score_day(**kw)

    def test_cutoff_and_each_provenance_timestamp(self):
        # Warsaw cutoff in winter is 08:50 UTC.
        for field in ('issued_at','publication_ts','retrieved_at'):
            kw=self.args(); kw[field]=datetime(2026,11,1,8,50,1,tzinfo=timezone.utc)
            with self.subTest(field=field), self.assertRaises(ValueError): score_day(**kw)
        kw=self.args(); kw['issued_at']=datetime(2026,11,1,8,50,tzinfo=timezone.utc)
        self.assertEqual(score_day(**kw).mae,0)

    def test_summer_cutoff_uses_warsaw_timezone(self):
        kw=self.args(date(2026,7,2)); kw['issued_at']=datetime(2026,7,1,7,51,tzinfo=timezone.utc)
        with self.assertRaises(ValueError): score_day(**kw)

    def test_naive_timestamp_rejected(self):
        kw=self.args(); kw['retrieved_at']=datetime(2026,11,1,7)
        with self.assertRaises(ValueError): score_day(**kw)

    def test_wrong_chronology_rejected(self):
        kw=self.args(); kw['publication_ts']=kw['retrieved_at']+timedelta(minutes=1)
        with self.assertRaises(ValueError): score_day(**kw)

    def test_repeat_is_deterministic_without_mutation(self):
        kw=self.args(); before=list(kw['forecast']); a=score_day(**kw); b=score_day(**kw)
        self.assertEqual(a,b); self.assertEqual(kw['forecast'],before)
        kw['actual_version']='second-verified-batch'
        self.assertNotEqual(a.identity,score_day(**kw).identity)
