from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import unittest
import test_shadow_ols as fixtures
from shadow_walkforward import run_walkforward,MODELS

class WalkforwardTests(unittest.TestCase):
    def args(self):
        history=fixtures.OLSTests().data(92)['history']
        return dict(history=history,target_days=[p['day'] for p in history[-2:]],
                    evaluated_at=datetime(2026,11,5,tzinfo=timezone.utc))

    def test_two_days_all_models_share_48_periods(self):
        kw = self.args()
        kw['history'][-1]['snapshots'].insert(0, None)
        out = run_walkforward(**kw)
        self.assertEqual(out['common_days'],2);self.assertEqual(len(out['ranking']),6)
        self.assertTrue(all(r['periods']==48 and r['days']==2 for r in out['ranking']))
        self.assertTrue(all(r['mae']<Decimal('1e-24') for r in out['ranking'] if r['model'].startswith('ols')))
        self.assertTrue(out['simulation'])

    def test_day_rejected_for_every_model_when_90d_window_incomplete(self):
        kw=self.args();kw['history']=kw['history'][1:]
        out=run_walkforward(**kw)
        self.assertEqual(out['common_days'],1)
        self.assertEqual(out['skipped'][0]['stage'],'ols90')
        self.assertTrue(all(r['periods']==24 for r in out['ranking']))

    def test_changing_own_actual_cannot_change_own_forecast(self):
        kw=self.args();kw['target_days']=kw['target_days'][:1];before=run_walkforward(**kw)
        target=next(p for p in kw['history'] if p['day']==kw['target_days'][0])
        target['actual']=[(p,v+1000) for p,v in target['actual']]
        after=run_walkforward(**kw)
        self.assertEqual(before['accepted'][0]['predictions'],after['accepted'][0]['predictions'])
        self.assertNotEqual(before['ranking'],after['ranking'])

    def test_no_available_target_actual_means_no_ranking(self):
        kw=self.args();kw['evaluated_at']=datetime(2026,1,1,tzinfo=timezone.utc)
        out=run_walkforward(**kw)
        self.assertEqual(out['common_days'],0);self.assertEqual(out['ranking'],[])
        self.assertEqual(len(out['skipped']),2)

    def test_mape_excludes_zero_wape_uses_absolute_actual(self):
        kw=self.args();kw['target_days']=kw['target_days'][-1:]
        target=kw['history'][-1];target['actual']=[(p,0 if i%2==0 else -100) for i,(p,v) in enumerate(target['actual'])]
        out=run_walkforward(**kw)
        self.assertTrue(all(r['mape_periods']==12 and r['zero_actual_periods']==12 for r in out['ranking']))
        self.assertTrue(all(r['wape_pct']>=0 for r in out['ranking']))

    def test_duplicate_packet_fails_and_inputs_are_unchanged(self):
        kw=self.args();before=deepcopy(kw);run_walkforward(**kw);self.assertEqual(kw,before)
        kw['history'].append(deepcopy(kw['history'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):run_walkforward(**kw)

    def test_dst_ranking_weights_49_periods_not_daily_means(self):
        history=fixtures.OLSTests().data(92,target=date(2026,10,26))['history']
        out=run_walkforward(history=history,target_days=[p['day'] for p in history[-2:]],
            evaluated_at=datetime(2026,11,5,tzinfo=timezone.utc))
        self.assertEqual(out['common_days'],2)
        self.assertTrue(all(r['periods']==49 for r in out['ranking']))
        total=Decimal(0);denom=Decimal(0)
        packets={p['day'].isoformat():p for p in history}
        for d in out['accepted']:
            actual=dict(packets[d['day']]['actual'])
            for p,v in d['predictions']['similar_level']['prices']:
                total+=abs(v-actual[p]);denom+=abs(actual[p])
        row=next(r for r in out['ranking'] if r['model']=='similar_level')
        self.assertLess(abs(row['mae']-total/49),Decimal('1e-24'))
        self.assertLess(abs(row['wape_pct']-100*total/denom),Decimal('1e-24'))

    def test_backdated_target_actual_is_rejected(self):
        kw = self.args()
        kw['target_days'] = kw['target_days'][-1:]
        kw['history'][-1]['actual_available_at'] = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
        for evaluated, reason in [
            (datetime(2026, 11, 1, 8, 49, tzinfo=timezone.utc), 'after evaluation time'),
            (kw['evaluated_at'], 'precedes simulated issuance'),
        ]:
            with self.subTest(evaluated=evaluated):
                out = run_walkforward(**{**kw, 'evaluated_at': evaluated})
                self.assertEqual(out['ranking'], [])
                self.assertIn(reason, out['skipped'][0]['reason'])

    def test_empty_run_does_not_manufacture_winner(self):
        out=run_walkforward(history=[],target_days=[],evaluated_at=datetime(2026,11,5,tzinfo=timezone.utc))
        self.assertEqual(out['ranking'],[]);self.assertEqual(out['common_days'],0)

    def test_target_snapshot_conflict_is_reported_before_model_fitting(self):
        args = self.args()
        target = args['history'][-1]
        original = target['snapshots'][0]
        conflict = deepcopy(original)
        conflict['id'] = 'conflicting-revision'
        conflict['rows'][0]['jwcd_mw'] += 1
        target['snapshots'].append(conflict)
        result = run_walkforward(**args)

        self.assertEqual(result['common_days'], 1)
        self.assertTrue(all(row['periods'] == 24 for row in result['ranking']))
        skipped, = result['skipped']
        self.assertEqual(skipped['day'], target['day'].isoformat())
        self.assertEqual(skipped['stage'], 'target_snapshot')
        self.assertEqual(dict(skipped['rejected_snapshots']), {
            str(original['id']): 'CONFLICTING_LATEST_SNAPSHOTS',
            'conflicting-revision': 'CONFLICTING_LATEST_SNAPSHOTS',
        })
