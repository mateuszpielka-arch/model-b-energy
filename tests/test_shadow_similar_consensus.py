from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
import unittest
import test_shadow_ols as fixtures
from shadow_similar_consensus import predict_similar,median_consensus,MEDIAN_MEMBERS
from shadow_scoring import WARSAW

class SimilarTests(unittest.TestCase):
    def args(self):
        kw=fixtures.OLSTests().data();kw.update(k=3,mode='level');return kw

    def test_level_selects_last_three_days_and_prices(self):
        kw=self.args();out=predict_similar(**kw)
        expected=[(kw['target_day']-timedelta(days=i)).isoformat() for i in (1,2,3)]
        self.assertEqual([d for d,_ in out['neighbors']],expected)
        self.assertEqual(out['prices'][0][1],Decimal(2*(1000+28*10)+10))

    def test_centered_shape_ties_use_most_recent(self):
        kw=self.args();kw['mode']='shape';out=predict_similar(**kw)
        self.assertTrue(all(dist==0 for _,dist in out['neighbors']))
        self.assertEqual(out['neighbors'][0][0],(kw['target_day']-timedelta(days=1)).isoformat())

    def test_future_day_and_order_do_not_change_neighbors(self):
        kw=self.args();a=predict_similar(**kw)
        kw['history'].reverse();kw['history'].append(dict(day=kw['target_day']))
        self.assertEqual(a,predict_similar(**kw))

    def test_missing_day_and_excessive_k_block(self):
        kw=self.args();kw['history'].pop()
        with self.assertRaises(ValueError):predict_similar(**kw)
        kw=self.args();kw['k']=100
        with self.assertRaises(ValueError):predict_similar(**kw)

    def test_late_actual_and_unproven_jwcd_block(self):
        for field in ('actual','jwcd'):
            kw=self.args()
            if field=='actual':kw['history'][0]['actual_available_at']+=timedelta(days=90)
            else:kw['history'][0]['snapshots'][0].pop('retrieval_completed_at')
            with self.subTest(field=field),self.assertRaises(ValueError):predict_similar(**kw)

    def test_dst_comparability_is_explicit(self):
        autumn=fixtures.OLSTests().data(target=date(2026,10,25))
        self.assertEqual(len(predict_similar(**autumn,k=3)['prices']),25)
        spring=fixtures.OLSTests().data(target=date(2026,3,29))
        for mode in ('shape', 'level'):
            baseline = predict_similar(**spring, k=3, mode=mode)
            self.assertEqual(len(baseline['prices']), 23)
            modified = deepcopy(spring)
            for packet in modified['history']:
                for row in packet['snapshots'][0]['rows']:
                    if row['period_start_utc'].astimezone(WARSAW).hour == 2:
                        row['jwcd_mw'] += 50000
            self.assertEqual(predict_similar(**modified, k=3, mode=mode), baseline)

    def test_input_not_mutated(self):
        kw=self.args();before=deepcopy(kw);predict_similar(**kw);self.assertEqual(kw,before)

class MedianTests(unittest.TestCase):
    def models(self):
        kw=fixtures.OLSTests().data(); base=predict_similar(**{**kw,'k':3})
        models={}
        for name,v in zip(MEDIAN_MEMBERS,('10','20','40','1000')):
            models[name]={**base,'model_version':'synthetic-'+name,'prices':tuple((p,Decimal(v)) for p,_ in base['prices'])}
        return kw['target_day'],models

    def test_actual_model_outputs_combine_on_same_snapshot(self):
        from shadow_ols import predict_ols
        kw=fixtures.OLSTests().data(90)
        models={f'ols{n}':predict_ols(**{**kw,'window_days':n}) for n in (30,60,90)}
        models['similar_shape']=predict_similar(**kw)
        out=median_consensus(target_day=kw['target_day'],models=models)
        self.assertEqual(len(out['prices']),24)
        self.assertEqual(len(out['members']),4)

    def test_four_member_median_rejects_outlier(self):
        day,models=self.models();out=median_consensus(target_day=day,models=models)
        self.assertTrue(all(v==30 for _,v in out['prices']))

    def test_missing_member_does_not_fall_back(self):
        day,models=self.models();models.pop('ols30')
        with self.assertRaises(ValueError):median_consensus(target_day=day,models=models)

    def test_wrong_snapshot_or_incomplete_periods_block(self):
        for mode in ('snapshot','period'):
            day,models=self.models()
            if mode=='snapshot':models['ols30']['target_snapshot_id']='other'
            else:models['ols30']['prices']=models['ols30']['prices'][:-1]
            with self.subTest(mode=mode),self.assertRaises(ValueError):median_consensus(target_day=day,models=models)
