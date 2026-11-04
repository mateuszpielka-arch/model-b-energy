from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
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
        self.assertEqual(out['accepted'][-1]['rejected_snapshots'], (('', 'INVALID_SNAPSHOT_OBJECT'),))

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
        self.assertEqual(out['common_periods'], 49)
        self.assertEqual([day['periods'] for day in out['accepted']], [24, 25])
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

    def test_ranked_day_reports_source_times_used_for_cutoff(self):
        args = self.args()
        snapshot = args['history'][-1]['snapshots'][0]
        result = run_walkforward(**args)
        day = result['accepted'][-1]

        self.assertEqual(day['target_snapshot_id'], str(snapshot['id']))
        self.assertEqual(
            datetime.fromisoformat(day['target_latest_publication_ts']),
            max(row['publication_ts'] for row in snapshot['rows']),
        )
        self.assertEqual(
            datetime.fromisoformat(day['target_retrieval_started_at']),
            snapshot['retrieved_at'],
        )
        completed = datetime.fromisoformat(day['target_retrieval_completed_at'])
        self.assertEqual(completed, snapshot['retrieval_completed_at'])
        self.assertLessEqual(completed, datetime.fromisoformat(day['simulated_issue_at']))

    def test_ranked_day_identifies_actual_version_and_availability(self):
        args = self.args()
        target = args['history'][-1]
        target['actual_version'] = 'verified-revision-2'
        result = run_walkforward(**args)
        day = result['accepted'][-1]

        self.assertEqual(day['actual_version'], 'verified-revision-2')
        available = datetime.fromisoformat(day['actual_available_at'])
        self.assertEqual(available, target['actual_available_at'])
        self.assertLess(datetime.fromisoformat(day["simulated_issue_at"]), available)
        self.assertLessEqual(available, args['evaluated_at'])

    def test_actual_available_at_exact_issuance_is_rejected(self):
        args = self.args()
        target = args['history'][-1]
        target['actual_available_at'] = datetime(2026, 11, 1, 8, 50, tzinfo=timezone.utc)
        args['target_days'] = [target['day']]
        result = run_walkforward(**args)
        self.assertEqual(result['ranking'], [])
        self.assertIn('coincides', result['skipped'][0]['reason'])

    def test_all_zero_actuals_keep_absolute_metrics_and_undefined_percentages(self):
        args = self.args()
        target = args['history'][-1]
        args['target_days'] = [target['day']]
        target['actual'] = [(period, Decimal(0)) for period, _ in target['actual']]

        result = run_walkforward(**args)

        self.assertEqual(result['common_days'], 1)
        self.assertEqual(len(result['ranking']), len(MODELS))
        for row in result['ranking']:
            with self.subTest(model=row['model']):
                self.assertEqual(row['zero_actual_periods'], 24)
                self.assertEqual(row['mape_periods'], 0)
                self.assertIsNone(row['mape_pct'])
                self.assertIsNone(row['wape_pct'])
                self.assertTrue(row['mae'].is_finite())
                self.assertTrue(row['bias'].is_finite())

    def test_equal_mae_shares_rank_and_next_rank_skips_tied_places(self):
        args = self.args()
        target = args['history'][-1]
        args['target_days'] = [target['day']]

        def forecast(error):
            return dict(model_version='test', prices=[
                (period, price + error) for period, price in target['actual']
            ])

        with patch('shadow_walkforward.predict_ols', side_effect=[
            forecast(1), forecast(-1), forecast(2),
        ]), patch('shadow_walkforward.predict_similar', return_value=forecast(3)), patch(
            'shadow_walkforward.median_consensus', return_value=forecast(4),
        ):
            ranking = run_walkforward(**args)['ranking']

        self.assertEqual([(row['model'], row['rank']) for row in ranking[:3]], [
            ('ols30', 1), ('ols60', 1), ('ols90', 3),
        ])
