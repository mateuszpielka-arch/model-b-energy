from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from fractions import Fraction
import unittest
from unittest.mock import patch
import test_shadow_ols as fixtures
from shadow_walkforward import run_walkforward,MODELS
from shadow_similar_consensus import predict_similar
from shadow_asof import cutoff_for_day
from shadow_scoring import score_day
from shadow_ols import predict_ols

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

    def test_training_report_contains_selected_publication_and_price_availability(self):
        args = self.args()
        args['target_days'] = args['target_days'][-1:]
        training = args['history'][-2]
        target_cutoff = cutoff_for_day(args['target_days'][0])
        training['actual_available_at'] = target_cutoff
        snapshot, = training['snapshots']
        revision = deepcopy(snapshot)
        revision['id'] = 'late-training-revision'
        revision['retrieval_completed_at'] = cutoff_for_day(training['day']) + timedelta(seconds=1)
        training['snapshots'].append(revision)

        accepted, = run_walkforward(**args)['accepted']
        self.assertTrue(all(
            timing['latest_training_actual_available_at_utc'] == target_cutoff.isoformat()
            for timing in accepted['scoring_input_timing'].values()
        ))
        for model in ('ols30', 'ols60', 'ols90', 'similar_shape', 'similar_level'):
            prediction = accepted['predictions'][model]
            evidence = prediction['training_timing'][-1]
            self.assertEqual(evidence['day'], training['day'].isoformat())
            self.assertEqual(evidence['actual_version'], training['actual_version'])
            self.assertEqual(evidence['actual_available_at_utc'], target_cutoff.isoformat())
            self.assertEqual(evidence['snapshot_id'], str(snapshot['id']))
            self.assertEqual(evidence['latest_publication_ts_utc'],
                             max(row['publication_ts'] for row in snapshot['rows']).isoformat())
            self.assertEqual(evidence['retrieval_completed_at_utc'],
                             snapshot['retrieval_completed_at'].isoformat())
            self.assertLessEqual(evidence['retrieval_completed_at_utc'], evidence['jwcd_cutoff_utc'])

    def test_ranking_is_identical_for_reversed_actual_rows(self):
        args = self.args()
        for packet in args['history'][-2:]:
            packet['actual'] = [(period, index * index + 137)
                                for index, (period, _) in enumerate(packet['actual'])]
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)

        for packet in args['history']:
            packet['actual'].reverse()
        self.assertEqual(run_walkforward(**args), expected)

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

    def test_out_of_window_payloads_are_not_read_for_earlier_ranking(self):
        args = self.args()
        target, future = args['target_days']
        args['target_days'] = [target]
        baseline = run_walkforward(**args)
        self.assertEqual(len(baseline['ranking']), len(MODELS))

        def unreadable():
            self.fail('An irrelevant history payload was read')
            yield

        for packet in args['history']:
            if packet['day'] == future:
                packet.update(snapshots=unreadable(), actual=unreadable())
        args['history'].append(dict(
            day=target - timedelta(days=91), snapshots=unreadable(), actual=unreadable(),
        ))
        self.assertEqual(run_walkforward(**args), baseline)

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

    def test_dst_ranking_pools_all_four_metrics_across_49_periods(self):
        history = fixtures.OLSTests().data(92, target=date(2026, 10, 26))['history']
        for packet in history[-2:]:
            first, second, *remaining = packet['actual']
            packet['actual'] = [(first[0], 0), (second[0], -50), *remaining]
        out = run_walkforward(
            history=history, target_days=[packet['day'] for packet in history[-2:]],
            evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(out['common_days'], 2)
        self.assertEqual(out['common_periods'], 49)
        self.assertEqual(out['ranking_metric'], 'mae')
        self.assertEqual([day['periods'] for day in out['accepted']], [24, 25])
        self.assertEqual(len(out['ranking']), len(MODELS))
        packets = {packet['day'].isoformat(): packet for packet in history}
        pairs_by_model = {model: [] for model in MODELS}
        for day in out['accepted']:
            actual = dict(packets[day['day']]['actual'])
            periods = [period.isoformat() for period in sorted(actual)]
            self.assertEqual(day['period_starts_utc'], periods)
            for model, prediction in day['predictions'].items():
                prices = prediction['prices']
                self.assertEqual(sorted(period.isoformat() for period, _ in prices), periods)
                pairs_by_model[model].extend((Fraction(price), Fraction(actual[p])) for p, price in prices)

        for row in out['ranking']:
            with self.subTest(model=row['model']):
                pairs = pairs_by_model[row['model']]
                self.assertEqual((row['periods'], row['mape_periods'], row['zero_actual_periods']), (49, 47, 2))
                absolute = sum(abs(price - actual) for price, actual in pairs)
                expected = {
                    'mae': absolute / 49,
                    'bias': sum(price - actual for price, actual in pairs) / 49,
                    'wape_pct': 100 * absolute / sum(abs(actual) for _, actual in pairs),
                    'mape_pct': 100 * sum(abs(price - actual) / abs(actual)
                                          for price, actual in pairs if actual) / 47,
                }
                for metric, value in expected.items():
                    exact = Decimal(value.numerator) / Decimal(value.denominator)
                    self.assertLess(abs(row[metric] - exact), Decimal('1e-22'), metric)

    def test_ranking_reports_common_coverage_across_all_delivery_day_lengths(self):
        spring = date(2026, 3, 29)
        autumn = date(2026, 10, 25)
        history = (
            fixtures.OLSTests().data(92, target=spring + timedelta(days=1))['history']
            + fixtures.OLSTests().data(92, target=autumn + timedelta(days=1))['history']
        )
        target_days = [autumn, autumn - timedelta(days=1), spring]
        result = run_walkforward(
            history=history, target_days=target_days,
            evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(result['common_days_by_hours'], {'23': 1, '24': 1, '25': 1})
        self.assertEqual(result['common_periods'], 72)
        self.assertEqual(result['skipped'], [])
        self.assertEqual([day['periods'] for day in result['accepted']], [23, 24, 25])
        self.assertEqual(len(result['ranking']), len(MODELS))
        for row in result['ranking']:
            self.assertEqual((row['days'], row['periods']), (3, 72))

    def test_wrong_utc_period_excludes_spring_dst_day_from_every_model(self):
        history = fixtures.OLSTests().data(92, target=date(2026, 3, 30))['history']
        target_days = [packet['day'] for packet in history[-2:]]
        spring = date(2026, 3, 29)

        def run():
            return run_walkforward(
                history=history, target_days=target_days,
                evaluated_at=datetime(2026, 3, 31, tzinfo=timezone.utc),
            )

        baseline = run()
        self.assertEqual(baseline['common_periods'], 47)
        self.assertEqual(baseline['accepted'][-1]['day'], spring.isoformat())
        self.assertEqual(len(baseline['accepted'][-1]['period_starts_utc']), 23)

        def wrong_period(**kwargs):
            prediction = predict_similar(**kwargs)
            if kwargs['mode'] == 'level' and kwargs['target_day'] == spring:
                first, *remaining = prediction['prices']
                period, price = first
                prediction['prices'] = [(period - timedelta(hours=1), price), *remaining]
            return prediction

        with patch('shadow_walkforward.predict_similar', side_effect=wrong_period):
            result = run()
        self.assertEqual(result['common_periods'], 24)
        self.assertEqual(result['common_days_by_hours'], {'23': 0, '24': 1, '25': 0})
        self.assertEqual(result['skipped_days_by_stage'], {'scoring': [spring.isoformat()]})
        self.assertEqual(result['skipped'][0]['model'], 'similar_level')
        self.assertEqual(result['skipped'][0]['reason'], 'Incomplete or mismatched delivery periods')
        self.assertEqual(len(result['ranking']), len(MODELS))
        self.assertTrue(all(row['periods'] == 24 for row in result['ranking']))

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
        target = args['history'][-1]
        args['target_days'] = [target['day']]
        snapshot, = target['snapshots']
        late = deepcopy(snapshot)
        late['id'] = 'late-target-revision'
        late['retrieval_completed_at'] = cutoff_for_day(target['day']) + timedelta(seconds=1)
        late['rows'][0]['publication_ts'] = late['retrieval_completed_at']
        target['snapshots'].insert(0, late)
        with patch('shadow_walkforward.score_day', wraps=score_day) as scorer:
            result = run_walkforward(**args)
        day, = result['accepted']

        self.assertEqual(scorer.call_count, len(MODELS))
        for call in scorer.call_args_list:
            self.assertEqual(call.kwargs['publication_ts'],
                             max(row['publication_ts'] for row in snapshot['rows']))
            self.assertEqual(call.kwargs['retrieved_at'], snapshot['retrieval_completed_at'])
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

    def test_scoring_uses_newer_training_timestamps_with_early_target_snapshot(self):
        args = self.args()
        training, target = args['history'][-2:]
        args['target_days'] = [target['day']]
        target_snapshot, = target['snapshots']
        early = cutoff_for_day(target['day']) - timedelta(days=2)
        target_snapshot['retrieved_at'] = early
        target_snapshot['retrieval_completed_at'] = early
        for row in target_snapshot['rows']:
            row['publication_ts'] = early
        source, = training['snapshots']
        publication = max(row['publication_ts'] for row in source['rows'])
        completed = source['retrieval_completed_at']

        with patch('shadow_walkforward.score_day', wraps=score_day) as scorer:
            accepted, = run_walkforward(**args)['accepted']

        self.assertEqual(accepted['target_latest_publication_ts'], early.isoformat())
        self.assertEqual(scorer.call_count, len(MODELS))
        for name, call in zip(MODELS, scorer.call_args_list):
            self.assertEqual(call.kwargs['publication_ts'], publication)
            self.assertEqual(call.kwargs['retrieved_at'], completed)
            self.assertEqual(accepted['scoring_input_timing'][name], {
                'latest_publication_ts_utc': publication.isoformat(),
                'latest_retrieval_completed_at_utc': completed.isoformat(),
                'latest_training_actual_available_at_utc': training['actual_available_at'].isoformat(),
            })

    def test_scoring_rejection_identifies_model_with_late_input(self):
        args = self.args()
        target = args['target_days'][-1]
        args['target_days'] = [target]

        def late_training(**kwargs):
            prediction = predict_ols(**kwargs)
            if kwargs['window_days'] == 60:
                evidence = prediction['training_timing'][-1]
                evidence['retrieval_completed_at_utc'] = (
                    cutoff_for_day(target) + timedelta(seconds=1)
                ).isoformat()
            return prediction

        with patch('shadow_walkforward.predict_ols', side_effect=late_training):
            result = run_walkforward(**args)
        self.assertEqual(result['ranking'], [])
        self.assertEqual(result['common_periods'], 0)
        self.assertEqual(result['skipped'], [{
            'day': target.isoformat(), 'stage': 'scoring', 'model': 'ols60',
            'reason': 'Invalid chronology or data unavailable before D-1 09:50',
        }])

    def test_scoring_excludes_common_day_if_model_reports_late_training_fix1(self):
        args = self.args()
        target = args['target_days'][-1]
        args['target_days'] = [target]

        def late_training(**kwargs):
            prediction = predict_ols(**kwargs)
            if kwargs['window_days'] == 60:
                prediction['training_timing'][-1]['actual_available_at_utc'] = (
                    cutoff_for_day(target) + timedelta(seconds=1)
                ).isoformat()
            return prediction

        with patch('shadow_walkforward.predict_ols', side_effect=late_training):
            result = run_walkforward(**args)
        self.assertEqual(result['ranking'], [])
        self.assertEqual(result['common_periods'], 0)
        self.assertEqual(result['skipped'], [{
            'day': target.isoformat(), 'stage': 'scoring', 'model': 'ols60',
            'reason': 'Training Fix1 prices unavailable at D-1 09:50',
        }])

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
            training = args['history'][-2]
            snapshot, = training['snapshots']
            timing = ({
                'actual_available_at_utc': training['actual_available_at'].isoformat(),
                'latest_publication_ts_utc': max(
                    row['publication_ts'] for row in snapshot['rows']
                ).isoformat(),
                'retrieval_completed_at_utc': snapshot['retrieval_completed_at'].isoformat(),
            },)
            return dict(model_version='test', training_timing=timing, prices=[
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

    def test_skipped_days_are_grouped_by_stage(self):
        args = self.args()
        target = args['history'][-1]
        del target['snapshots']
        missing_day = target['day'] + timedelta(days=1)
        args['target_days'].append(missing_day)

        result = run_walkforward(**args)

        self.assertEqual(result['common_days'], 1)
        self.assertEqual(result['skipped_days_by_stage'], {
            'target_snapshot': [target['day'].isoformat()],
            'actual': [missing_day.isoformat()],
        })

    def test_missing_historical_snapshots_skip_affected_target(self):
        args = self.args()
        args['target_days'] = args['target_days'][-1:]
        del args['history'][-2]['snapshots']

        result = run_walkforward(**args)

        self.assertEqual(result['ranking'], [])
        skipped, = result['skipped']
        self.assertEqual(skipped['stage'], 'ols30')
        self.assertEqual(skipped['reason'],
                         f"Historical JWCD availability not proven: {args['history'][-2]['day']}")

    def test_invalid_snapshot_collection_does_not_abort_backtest(self):
        args = self.args()
        target = args['history'][-1]
        target['snapshots'] = 42

        result = run_walkforward(**args)

        self.assertEqual(result['common_days'], 1)
        skipped, = result['skipped']
        self.assertEqual(skipped['stage'], 'target_snapshot')
        self.assertEqual(skipped['rejected_snapshots'], (('', 'INVALID_SNAPSHOT_COLLECTION'),))

    def test_missing_target_prices_skip_only_affected_day(self):
        args = self.args()
        del args['history'][-1]['actual']
        result = run_walkforward(**args)
        self.assertEqual(result['common_days'], 1)
        skipped, = result['skipped']
        self.assertEqual(skipped['reason'], 'Incomplete target actual periods')

    def test_missing_training_prices_skip_affected_day(self):
        args = self.args()
        args['target_days'] = args['target_days'][-1:]
        del args['history'][-2]['actual']
        result = run_walkforward(**args)
        self.assertEqual(result['ranking'], [])
        skipped, = result['skipped']
        self.assertEqual(skipped['reason'],
                         f"Historical actual incomplete or invalid periods: {args['history'][-2]['day']}")

    def test_malformed_target_prices_skip_only_affected_day(self):
        for prices, reason in ((None, 'Invalid price collection'),
                               ([None], 'Invalid price row')):
            with self.subTest(prices=prices):
                args = self.args()
                args['history'][-1]['actual'] = prices
                result = run_walkforward(**args)
                self.assertEqual(result['common_days'], 1)
                skipped, = result['skipped']
                self.assertEqual(skipped['reason'], reason)

    def test_late_training_snapshot_reason_is_reported(self):
        args = self.args()
        args['target_days'] = args['target_days'][-1:]
        training = args['history'][-2]
        training['snapshots'][0]['retrieval_completed_at'] += timedelta(days=1)
        skipped, = run_walkforward(**args)['skipped']
        self.assertIn(training['day'].isoformat(), skipped['reason'])
        self.assertIn('RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER', skipped['reason'])

    def test_snapshot_iterators_match_lists_for_training_and_targets(self):
        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected["common_days"], 2)
        args["history"] = [
            {**packet, "snapshots": iter(packet["snapshots"])}
            for packet in args["history"]
        ]
        self.assertEqual(run_walkforward(**args), expected)

    def test_snapshot_row_iterators_match_lists_across_models_and_days(self):
        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)
        for packet in args['history']:
            for snapshot in packet['snapshots']:
                snapshot['rows'] = iter(snapshot['rows'])

        self.assertEqual(run_walkforward(**args), expected)

    def test_unreadable_and_late_snapshot_rows_are_ignored_across_models(self):
        args = self.args()
        expected = run_walkforward(**args)
        target = args['history'][-1]
        original = target['snapshots'][0]

        def broken():
            yield original['rows'][0]
            raise OSError('Unreadable snapshot')

        def late():
            self.fail('Post-cutoff rows were read')
            yield

        target['snapshots'] += [
            {**original, 'id': 'broken', 'rows': broken()},
            {**original, 'id': 'late', 'rows': late(),
             'retrieval_completed_at': cutoff_for_day(target['day']) + timedelta(seconds=1)},
        ]

        result = run_walkforward(**args)
        self.assertEqual(result['ranking'], expected['ranking'])
        self.assertEqual(result['accepted'][-1]['predictions'], expected['accepted'][-1]['predictions'])
        self.assertEqual(dict(result['accepted'][-1]['rejected_snapshots']), {
            'broken': 'UNREADABLE_SNAPSHOT_ROWS',
            'late': 'RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER',
        })

    def test_actual_iterators_match_lists_for_training_and_targets(self):
        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)
        args['history'] = [
            {**packet, 'actual': iter(packet['actual'])}
            for packet in args['history']
        ]

        self.assertEqual(run_walkforward(**args), expected)

    def test_unreadable_iterator_rejects_only_dependent_delivery_days(self):
        def broken_after_first(rows):
            yield rows[0]
            raise ValueError('Broken source record')

        for packet_index, field, rejected_index in ((-1, 'actual', -1), (0, 'snapshots', 0)):
            with self.subTest(field=field):
                args = self.args()
                packet = args['history'][packet_index]
                packet[field] = broken_after_first(packet[field])
                rejected_day = args['target_days'][rejected_index]
                other_day = next(day for day in args['target_days'] if day != rejected_day)

                result = run_walkforward(**args)

                self.assertEqual(result['unreadable_packets'], {packet['day'].isoformat(): field})
                self.assertEqual([entry['day'] for entry in result['accepted']], [other_day.isoformat()])
                skipped, = result['skipped']
                self.assertEqual(skipped['day'], rejected_day.isoformat())
                if packet['day'] == rejected_day:
                    self.assertEqual(skipped['stage'], 'packet')
                    self.assertIn(field, skipped['reason'])
                else:
                    self.assertIn(f'first missing day {packet["day"]}', skipped['reason'])

    def test_scoring_reuses_validated_target_prices(self):
        args = self.args()
        target = args['history'][-1]
        args['target_days'] = [target['day']]
        expected = run_walkforward(**args)
        target['actual'] = iter(target['actual'])

        result = run_walkforward(**args)

        self.assertEqual(result['common_days'], 1)
        self.assertEqual(result['accepted'][0]['scores'], expected['accepted'][0]['scores'])
        self.assertEqual(result['ranking'], expected['ranking'])
