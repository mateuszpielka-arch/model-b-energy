from copy import deepcopy
from datetime import date,datetime,time,timedelta
from decimal import Decimal
import unittest
from shadow_scoring import WARSAW,_utc,expected_periods
from shadow_ols import predict_ols

class OLSTests(unittest.TestCase):
    def data(self,n=30,target=date(2026,11,3)):
        def snapshot(day,index):
            cutoff=_utc(datetime.combine(day-timedelta(days=1),time(9,50),WARSAW))
            return dict(id=day.isoformat(),delivery_date=day,retrieved_at=cutoff-timedelta(hours=1),
                retrieval_completed_at=cutoff-timedelta(minutes=59),
                rows=[dict(period_start_utc=p,publication_ts=cutoff-timedelta(hours=2),
                           jwcd_mw=1000+index*10+p.astimezone(WARSAW).hour)
                      for p in expected_periods(day)])
        history=[]
        for index in range(n):
            day=target-timedelta(days=n-index); snap=snapshot(day,index)
            history.append(dict(day=day,snapshots=[snap],actual_verified=True,actual_version=day.isoformat(),
                actual_available_at=_utc(datetime.combine(day-timedelta(days=1),time(11),WARSAW)),
                actual=[(r['period_start_utc'],2*r['jwcd_mw']+10) for r in snap['rows']]))
        return dict(target_day=target,target_snapshots=[snapshot(target,n)],history=history,window_days=n)

    def test_recovers_known_relation_all_windows(self):
        for n in (30,60,90):
            kw=self.data(n);out=predict_ols(**kw)
            with self.subTest(n=n):
                self.assertEqual(len(out['training_evidence']),n)
                self.assertTrue(all(abs(c['slope']-2)<Decimal('1e-25') and abs(c['intercept']-10)<Decimal('1e-25') for c in out['coefficients'].values()))
                self.assertLess(abs(out['prices'][0][1]-Decimal(2*(1000+n*10)+10)),Decimal('1e-25'))

    def test_future_actual_injection_does_not_change_result(self):
        kw=self.data();before=predict_ols(**kw)
        kw['history'].append(dict(day=kw['target_day'],actual=[('future',999999)]))
        self.assertEqual(predict_ols(**kw),before)

    def test_one_missing_day_blocks_fit(self):
        kw=self.data();kw['history'].pop()
        with self.assertRaisesRegex(ValueError,'Incomplete'):predict_ols(**kw)

    def test_late_actual_blocks_fit(self):
        kw=self.data();kw['history'][0]['actual_available_at']=datetime.combine(kw['target_day'],time(),WARSAW)
        with self.assertRaisesRegex(ValueError,'unavailable'):predict_ols(**kw)

    def test_revised_jwcd_after_historical_cutoff_is_excluded(self):
        kw=self.data();before=predict_ols(**kw)
        s=deepcopy(kw['history'][0]['snapshots'][0]);s['id']='late-revision'
        s['retrieval_completed_at']+=timedelta(days=2)
        for r in s['rows']:r['jwcd_mw']=999999
        kw['history'][0]['snapshots'].append(s)
        self.assertEqual(predict_ols(**kw),before)

    def test_unverified_actual_and_missing_period_block_fit(self):
        for mode in ('unverified','missing'):
            kw=self.data()
            if mode=='unverified':kw['history'][0]['actual_verified']=False
            else:kw['history'][0]['actual'].pop()
            with self.subTest(mode=mode),self.assertRaises(ValueError):predict_ols(**kw)

    def test_constant_jwcd_has_no_silent_fallback(self):
        kw=self.data()
        for p in kw['history']:
            for r in p['snapshots'][0]['rows']:r['jwcd_mw']=1000
        with self.assertRaisesRegex(ValueError,'Constant'):predict_ols(**kw)

    def test_dst_target_preserves_utc_periods(self):
        for target,n in [(date(2026,10,25),25),(date(2026,3,29),23)]:
            out=predict_ols(**self.data(target=target))
            with self.subTest(target=target):
                self.assertEqual(len(out['prices']),n)
                self.assertEqual(len(dict(out['prices'])),n)

    def test_no_input_mutation_and_duplicate_date_rejected(self):
        kw=self.data();before=deepcopy(kw);predict_ols(**kw);self.assertEqual(kw,before)
        kw['history'].append(deepcopy(kw['history'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):predict_ols(**kw)

    def test_target_after_cutoff_blocked(self):
        kw=self.data();kw['target_snapshots'][0]['retrieval_completed_at']+=timedelta(days=1)
        with self.assertRaisesRegex(ValueError,'target JWCD'):predict_ols(**kw)

    def test_backdated_historical_actual_blocks_fit_before_reading_payloads(self):
        from shadow_asof import cutoff_for_day

        for delta in (timedelta(seconds=-1), timedelta(0)):
            with self.subTest(delta=delta):
                args = self.data()
                packet = args['history'][0]
                packet['actual_available_at'] = cutoff_for_day(packet['day']) + delta

                def unreadable():
                    self.fail('Payload of a backdated historical day was read')
                    yield

                packet['snapshots'] = unreadable()
                packet['actual'] = unreadable()
                with self.assertRaisesRegex(ValueError, 'at or before its simulated issuance'):
                    predict_ols(**args)
