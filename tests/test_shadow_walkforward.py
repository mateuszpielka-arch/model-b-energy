from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal, Inexact, ROUND_UP, localcontext
from fractions import Fraction
import unittest
from unittest.mock import patch
import test_shadow_ols as fixtures
from shadow_walkforward import FROZEN_90D_VERSION, MODELS, run_walkforward
from shadow_similar_consensus import median_consensus, predict_similar
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

    def test_archived_frozen_90d_is_a_separate_paired_benchmark(self):
        monday = date(2026, 10, 26)
        history = fixtures.OLSTests().data(93, target=monday + timedelta(days=1))['history']
        targets = history[-3:]
        args = dict(history=history, target_days=[packet['day'] for packet in targets],
                    evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc))
        records = {}
        actual_values = []
        for packet, offset in zip(targets, (10, -30, 1000), strict=True):
            day = packet['day']
            cutoff = cutoff_for_day(day)
            prices = [(period, price + offset) for period, price in packet['actual']]
            records[day] = dict(
                forecast_id=f'frozen:{day}', model_version=FROZEN_90D_VERSION,
                forecast_status='FROZEN', issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
                retrieved_at=cutoff, prices=iter(prices),
            )
            if day != monday:
                actual_values.extend((abs(price), offset) for _, price in packet['actual'])
        records[monday]['issued_at'] += timedelta(seconds=1)

        def unreadable_late_prices():
            self.fail('Late FROZEN forecast prices were consumed before the cutoff check')
            yield

        records[monday]['prices'] = unreadable_late_prices()

        baseline = run_walkforward(**args)
        result = run_walkforward(**args, frozen_90d_by_day=records)
        benchmark = result.pop('frozen_90d_benchmark')
        self.assertEqual(result, baseline)
        self.assertEqual([pair['day'] for pair in benchmark['paired_days']],
                         [packet['day'].isoformat() for packet in targets[:2]])
        for pair, accepted in zip(benchmark['paired_days'], result['accepted'][:2], strict=True):
            self.assertEqual(pair['ols90'], accepted['scores']['ols90'])
            self.assertEqual(pair['frozen_90d'].actual_version, pair['ols90'].actual_version)
        self.assertEqual(set(benchmark['excluded_days']), {monday.isoformat()})
        self.assertIn('chronology', benchmark['excluded_days'][monday.isoformat()])

        ols, frozen = benchmark['ranking']
        self.assertEqual([ols['model'], frozen['model']], ['ols90', 'frozen_90d'])
        for row in (ols, frozen):
            self.assertEqual((row['days'], row['periods'], row['mape_periods']), (2, 49, 49))
        absolute_error = 24 * 10 + 25 * 30
        ratios = sum(Fraction(abs(error), actual) for actual, error in actual_values)
        for metric, expected in (
            ('mae', Decimal(absolute_error) / 49),
            ('bias', Decimal(24 * 10 - 25 * 30) / 49),
            ('wape_pct', Decimal(100 * absolute_error) / sum(actual for actual, _ in actual_values)),
            ('mape_pct', Decimal(100 * ratios.numerator) / (49 * ratios.denominator)),
        ):
            self.assertLess(abs(frozen[metric] - expected), Decimal('1e-24'), metric)

    def test_interrupted_frozen_prices_preserve_shadow_ranking_and_other_benchmark_day(self):
        autumn = date(2026, 10, 25)
        history = fixtures.OLSTests().data(92, target=autumn + timedelta(days=1))['history']
        earlier, autumn_packet = history[-2:]
        args = dict(history=history, target_days=[earlier['day'], autumn],
                    evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc))
        baseline = run_walkforward(**args)
        self.assertEqual(baseline['common_periods'], 49)

        records = {}
        for packet in (earlier, autumn_packet):
            day = packet['day']
            cutoff = cutoff_for_day(day)
            records[day] = dict(
                forecast_id=f'frozen:{day}', model_version=FROZEN_90D_VERSION,
                forecast_status='FROZEN', issued_at=cutoff, publication_ts=cutoff - timedelta(minutes=1),
                retrieved_at=cutoff, prices=packet['actual'],
            )

        def interrupted_prices():
            yield autumn_packet['actual'][0]
            raise OSError('Forecast export ended unexpectedly')

        records[autumn]['prices'] = interrupted_prices()
        result = run_walkforward(**args, frozen_90d_by_day=records)
        benchmark = result.pop('frozen_90d_benchmark')
        self.assertEqual(result, baseline)
        self.assertEqual(benchmark['excluded_days'], {autumn.isoformat(): 'Unreadable archived FROZEN forecast prices'})
        self.assertEqual([pair['day'] for pair in benchmark['paired_days']], [earlier['day'].isoformat()])
        self.assertEqual([row['periods'] for row in benchmark['ranking']], [24, 24])
        self.assertEqual(benchmark['coverage_by_hours'], {
            '23': dict(shadow_days=0, paired_days=0, unpaired_days=0, paired_periods=0),
            '24': dict(shadow_days=1, paired_days=1, unpaired_days=0, paired_periods=24),
            '25': dict(shadow_days=1, paired_days=0, unpaired_days=1, paired_periods=0),
        })

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

    def test_invalid_fix1_version_blocks_target_and_training_without_reading_payload(self):
        history = fixtures.OLSTests().data(93)['history']
        first, invalid, later = history[-3:]

        def unreadable():
            self.fail('Unverified Fix1 packet payload was consumed')
            yield

        invalid.update(actual_version=42, actual=unreadable(), snapshots=unreadable())
        report = run_walkforward(
            history=history, target_days=[first['day'], invalid['day'], later['day']],
            evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )

        self.assertEqual([entry['day'] for entry in report['accepted']], [first['day'].isoformat()])
        self.assertEqual(report['common_periods'], 24)
        self.assertEqual(report['skipped'], [
            dict(day=invalid['day'].isoformat(), stage='actual', reason='Unverified target actual'),
            dict(day=later['day'].isoformat(), stage='ols30',
                 reason=f"Historical actual provenance not verified: {invalid['day']}"),
        ])

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

    def test_training_only_payload_is_unread_when_late_for_every_relevant_target(self):
        args = self.args()
        first, second = args['target_days']
        packet = next(row for row in args['history'] if row['day'] == first - timedelta(days=1))
        packet['actual_available_at'] = cutoff_for_day(second) + timedelta(seconds=1)
        # A later target outside the packet's 90-day window cannot authorize its use.
        args['target_days'].append(second + timedelta(days=90))
        baseline = run_walkforward(**args)
        self.assertEqual(baseline['ranking'], [])
        self.assertEqual([item['reason'] for item in baseline['skipped']], [
            f"Historical actual unavailable at target cutoff: {packet['day']}",
            f"Historical actual unavailable at target cutoff: {packet['day']}",
            'Missing target packet',
        ])

        def unreadable():
            self.fail('Training data unavailable at every applicable cutoff was read')
            yield

        actual, snapshots = packet['actual'], packet['snapshots']
        packet.update(actual=unreadable(), snapshots=unreadable())
        self.assertEqual(run_walkforward(**args), baseline)

        packet.update(actual=iter(actual), snapshots=iter(snapshots),
                      actual_available_at=cutoff_for_day(second))
        at_cutoff = run_walkforward(**args)
        self.assertEqual([item['day'] for item in at_cutoff['accepted']], [second.isoformat()])
        self.assertEqual(at_cutoff['skipped'][0], baseline['skipped'][0])
        self.assertEqual(at_cutoff['common_periods'], 24)

    def test_future_issuance_does_not_authorize_reading_training_only_packets(self):
        args = self.args()
        earlier, future = args['target_days']
        # Supply one earlier target and a second target whose cutoff has not arrived.
        future += timedelta(days=1)
        args['target_days'] = [earlier, future]
        args['evaluated_at'] = cutoff_for_day(future) - timedelta(seconds=1)
        training_only = args['history'][-1]
        self.assertGreater(training_only['day'], earlier)
        self.assertLess(training_only['actual_available_at'], args['evaluated_at'])

        baseline = run_walkforward(**args)
        self.assertEqual([entry['day'] for entry in baseline['accepted']], [earlier.isoformat()])
        self.assertEqual(baseline['common_periods'], 24)
        self.assertEqual(baseline['skipped'][0]['reason'], 'Missing target packet')

        def unreadable():
            self.fail('Training data was read for a forecast not yet issued')
            yield

        training_only.update(actual=unreadable(), snapshots=unreadable())
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
        self.assertEqual(out['ranking_by_day_type'], {'weekday': [], 'weekend': out['ranking']})
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

    def test_dst_walkforward_ignores_caller_decimal_rounding_and_traps(self):
        autumn = date(2026, 10, 25)
        history = fixtures.OLSTests().data(92, target=autumn + timedelta(days=1))['history']
        targets = [packet['day'] for packet in history[-2:]]
        cutoff = cutoff_for_day(autumn)
        archived = {
            autumn: dict(
                forecast_id=f'frozen:{autumn}', model_version=FROZEN_90D_VERSION,
                forecast_status='FROZEN', issued_at=cutoff, publication_ts=cutoff,
                retrieved_at=cutoff,
                prices=[(period, '1000') for period, _ in history[-1]['actual']],
            ),
        }
        args = dict(history=history, target_days=targets, frozen_90d_by_day=archived,
                    evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc))
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_periods'], 49)
        self.assertEqual(expected['frozen_90d_benchmark']['coverage_by_hours']['25']['paired_periods'], 25)

        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            self.assertEqual(run_walkforward(**args), expected)

    def test_ranking_reports_common_coverage_across_all_delivery_day_lengths(self):
        spring = date(2026, 3, 29)
        autumn = date(2026, 10, 25)
        history = (
            fixtures.OLSTests().data(92, target=spring + timedelta(days=1))['history']
            + fixtures.OLSTests().data(92, target=autumn + timedelta(days=1))['history']
        )
        target_days = [autumn, autumn - timedelta(days=1), spring]
        autumn_packet = next(packet for packet in history if packet['day'] == autumn)
        repeated_period = datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
        autumn_packet['actual'] = [
            (period, 0 if period == repeated_period else price)
            for period, price in autumn_packet['actual']
        ]
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

        for day in result['accepted']:
            hours = day['periods']
            ranking = result['ranking_by_hours'][str(hours)]
            self.assertEqual({row['model'] for row in ranking}, set(MODELS))
            self.assertEqual([row['mae'] for row in ranking], sorted(row['mae'] for row in ranking))
            for row in ranking:
                with self.subTest(hours=hours, model=row['model']):
                    score = day['scores'][row['model']]
                    self.assertEqual((row['days'], row['periods']), (1, hours))
                    for metric in ('mae', 'bias', 'mape_pct', 'wape_pct', 'mape_periods', 'zero_actual_periods'):
                        self.assertEqual(row[metric], getattr(score, metric))

        by_hour = result['ranking_by_local_hour']
        self.assertEqual(set(by_hour), {f'{hour:02d}:00' for hour in range(24)} | {'02:00[fold=1]'})
        self.assertEqual(sum(rows[0]['periods'] for rows in by_hour.values()), 72)
        self.assertEqual({(row['days'], row['periods'], row['mape_periods'])
                          for row in by_hour['02:00']}, {(2, 2, 2)})
        repeated = by_hour['02:00[fold=1]']
        self.assertEqual({row['model'] for row in repeated}, set(MODELS))
        for row in repeated:
            price = dict(result['accepted'][-1]['predictions'][row['model']]['prices'])[repeated_period]
            self.assertEqual((row['days'], row['periods'], row['mape_periods']), (1, 1, 0))
            self.assertEqual(row['mae'], price.copy_abs())
            self.assertEqual(row['bias'], price)
            self.assertIsNone(row['mape_pct'])
            self.assertIsNone(row['wape_pct'])

    def test_ranking_separates_warsaw_delivery_weekdays_from_weekends(self):
        monday = date(2026, 10, 26)
        history = fixtures.OLSTests().data(93, target=monday + timedelta(days=1))['history']
        target = history[-1]
        period, _ = target['actual'][0]
        target['actual'][0] = (period, 0)
        result = run_walkforward(
            history=history, target_days=[packet['day'] for packet in history[-3:]],
            evaluated_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(result['common_periods'], 73)
        weekday, weekend = (result['ranking_by_day_type'][kind] for kind in ('weekday', 'weekend'))
        for ranking, days, periods, zeros in ((weekday, 1, 24, 1), (weekend, 2, 49, 0)):
            self.assertEqual({row['model'] for row in ranking}, set(MODELS))
            for row in ranking:
                with self.subTest(model=row['model'], days=days):
                    self.assertEqual((row['days'], row['periods'], row['zero_actual_periods']),
                                     (days, periods, zeros))
        monday_result = result['accepted'][-1]
        for row in weekday:
            self.assertEqual(row['mae'], monday_result['scores'][row['model']].mae)

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
        self.assertEqual(result['ranking_by_hours'], {'23': [], '24': result['ranking'], '25': []})
        self.assertEqual(result['skipped_days_by_stage'], {'scoring': [spring.isoformat()]})
        self.assertEqual(result['skipped'][0]['model'], 'similar_level')
        self.assertEqual(result['skipped'][0]['reason'], 'Incomplete or mismatched delivery periods')
        self.assertEqual(len(result['ranking']), len(MODELS))
        self.assertTrue(all(row['periods'] == 24 for row in result['ranking']))

    def test_median_scoring_and_ranking_reuse_single_pass_forecast_prices(self):
        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)

        def lazy_ols(**kwargs):
            prediction = predict_ols(**kwargs)
            return {**prediction, 'prices': iter(prediction['prices'])}

        def lazy_similar(**kwargs):
            prediction = predict_similar(**kwargs)
            return {**prediction, 'prices': iter(prediction['prices'])}

        def lazy_median(**kwargs):
            prediction = median_consensus(**kwargs)
            return {**prediction, 'prices': iter(prediction['prices'])}

        with patch('shadow_walkforward.predict_ols', side_effect=lazy_ols), patch(
            'shadow_walkforward.predict_similar', side_effect=lazy_similar,
        ), patch('shadow_walkforward.median_consensus', side_effect=lazy_median):
            result = run_walkforward(**args)

        self.assertEqual(result['common_periods'], 48)
        self.assertEqual(result['ranking'], expected['ranking'])
        self.assertEqual(result['accepted'], expected['accepted'])

    def test_wrong_target_snapshot_excludes_day_from_all_models_before_scoring(self):
        args = self.args()
        affected = args['target_days'][-1]
        other = args['target_days'][0]

        def wrong_snapshot(**kwargs):
            prediction = predict_similar(**kwargs)
            if kwargs['mode'] == 'level' and kwargs['target_day'] == affected:
                prediction['target_snapshot_id'] = 'another-revision'
            return prediction

        with patch('shadow_walkforward.predict_similar', side_effect=wrong_snapshot), patch(
            'shadow_walkforward.score_day', wraps=score_day,
        ) as scoring:
            result = run_walkforward(**args)

        self.assertEqual([call.kwargs['day'] for call in scoring.call_args_list], [other] * len(MODELS))
        self.assertEqual(result['common_periods'], 24)
        self.assertEqual([row['periods'] for row in result['ranking']], [24] * len(MODELS))
        self.assertEqual(result['skipped'], [{
            'day': affected.isoformat(), 'stage': 'scoring', 'model': 'similar_level',
            'reason': 'Prediction does not use the selected target JWCD snapshot',
        }])

    def test_backdated_target_actual_is_rejected(self):
        kw = self.args()
        kw['target_days'] = kw['target_days'][-1:]
        target = kw['history'][-1]
        target['actual_available_at'] = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)

        def unreadable():
            self.fail('Payload with backdated Fix1 availability was read')
            yield

        target.update(snapshots=unreadable(), actual=unreadable())
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

    def test_scoring_checks_training_jwcd_against_its_own_cutoff(self):
        args = self.args()
        training, target = args['history'][-2:]
        args['target_days'] = [target['day']]
        training_cutoff = cutoff_for_day(training['day'])

        for field, offset, rejected in (
            ('retrieval_completed_at_utc', 0, False),
            ('retrieval_completed_at_utc', 1, True),
            ('latest_publication_ts_utc', 1, True),
            ('retrieved_at_utc', 1, True),
        ):
            with self.subTest(field=field, offset=offset):
                def change_training_timing(**kwargs):
                    prediction = predict_ols(**kwargs)
                    if kwargs['window_days'] == 60:
                        evidence = prediction['training_timing'][-1]
                        evidence['retrieval_completed_at_utc'] = training_cutoff.isoformat()
                        evidence[field] = (training_cutoff + timedelta(seconds=offset)).isoformat()
                    return prediction

                with patch('shadow_walkforward.predict_ols', side_effect=change_training_timing):
                    result = run_walkforward(**args)

                if rejected:
                    self.assertEqual(result['ranking'], [])
                    self.assertEqual(result['common_periods'], 0)
                    self.assertEqual(result['skipped'], [{
                        'day': target['day'].isoformat(), 'stage': 'scoring', 'model': 'ols60',
                        'reason': f"Training JWCD chronology invalid at D-1 09:50: {training['day']}",
                    }])
                else:
                    self.assertEqual(result['common_periods'], 24)
                    self.assertEqual(len(result['ranking']), len(MODELS))

    def test_scoring_checks_training_delivery_and_own_fix1_cutoff(self):
        args = self.args()
        training, target = args['history'][-2:]
        args['target_days'] = [target['day']]
        training_cutoff = cutoff_for_day(training['day'])
        for changes, expected_reason in (
            ({'day': target['day'].isoformat()},
             f"Training delivery must precede target delivery: {target['day']}"),
            ({'day': (target['day'] + timedelta(days=1)).isoformat()},
             'Training delivery must precede target delivery: ' +
             (target['day'] + timedelta(days=1)).isoformat()),
            ({'actual_available_at_utc': training_cutoff.isoformat()},
             f"Training Fix1 available at or before its own D-1 09:50: {training['day']}"),
            ({'actual_available_at_utc': (training_cutoff + timedelta(seconds=1)).isoformat()},
             None),
        ):
            with self.subTest(changes=changes):
                def change_training(**kwargs):
                    prediction = predict_ols(**kwargs)
                    if kwargs['window_days'] == 60:
                        prediction['training_timing'][-1].update(changes)
                    return prediction

                with patch('shadow_walkforward.predict_ols', side_effect=change_training), patch(
                    'shadow_walkforward.score_day', wraps=score_day,
                ) as scoring:
                    result = run_walkforward(**args)

                if expected_reason is None:
                    self.assertEqual(result['common_periods'], 24)
                    self.assertEqual(scoring.call_count, len(MODELS))
                else:
                    scoring.assert_not_called()
                    self.assertEqual(result['ranking'], [])
                    self.assertEqual(result['skipped'], [{
                        'day': target['day'].isoformat(), 'stage': 'scoring',
                        'model': 'ols60', 'reason': expected_reason,
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
                'day': training['day'].isoformat(),
                'retrieved_at_utc': snapshot['retrieved_at'].isoformat(),
                'actual_available_at_utc': training['actual_available_at'].isoformat(),
                'latest_publication_ts_utc': max(
                    row['publication_ts'] for row in snapshot['rows']
                ).isoformat(),
                'retrieval_completed_at_utc': snapshot['retrieval_completed_at'].isoformat(),
            },)
            return dict(
                model_version='test', training_timing=timing,
                target_snapshot_id=target['snapshots'][0]['id'],
                prices=[(period, price + error) for period, price in target['actual']],
            )

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

    def test_lazy_daily_collections_are_read_once_for_scoring_and_training(self):
        class ReloadableCollection:
            def __init__(self, values):
                self.values = values
                self.reads = 0

            def __iter__(self):
                self.reads += 1
                return iter(self.values if self.reads == 1 else ())

        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)
        sources = []
        for packet in args['history'][-3:]:
            for field in ('snapshots', 'actual'):
                source = ReloadableCollection(packet[field])
                packet[field] = source
                sources.append(source)

        self.assertEqual(run_walkforward(**args), expected)
        self.assertEqual([source.reads for source in sources], [1] * len(sources))

    def test_snapshot_row_iterators_match_lists_across_models_and_days(self):
        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)
        for packet in args['history']:
            for snapshot in packet['snapshots']:
                snapshot['rows'] = iter(snapshot['rows'])

        self.assertEqual(run_walkforward(**args), expected)

    def test_lazy_snapshot_rows_are_read_once_across_models_and_days(self):
        class ReloadableRows:
            def __init__(self, rows):
                self.rows = rows
                self.reads = 0

            def __iter__(self):
                self.reads += 1
                if self.reads == 1:
                    return iter(self.rows)
                return iter({**row, 'jwcd_mw': row['jwcd_mw'] + 50} for row in self.rows)

        args = self.args()
        expected = run_walkforward(**args)
        self.assertEqual(expected['common_days'], 2)
        sources = []
        for packet in args['history'][-3:]:
            snapshot, = packet['snapshots']
            source = ReloadableRows(snapshot['rows'])
            snapshot['rows'] = source
            sources.append(source)

        self.assertEqual(run_walkforward(**args), expected)
        self.assertEqual([source.reads for source in sources], [1, 1, 1])

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

    def test_unavailable_target_payload_is_not_read_before_evaluation(self):
        args = self.args()
        earlier, target = args['history'][-2:]
        args['evaluated_at'] = target['actual_available_at'] - timedelta(seconds=1)
        baseline = run_walkforward(**args)
        self.assertEqual([entry['day'] for entry in baseline['accepted']], [earlier['day'].isoformat()])
        self.assertEqual(baseline['skipped'][0]['reason'], 'Target actual not yet available for evaluation')

        def unavailable():
            self.fail('Payload was read before its availability time')
            yield

        target.update(snapshots=unavailable(), actual=unavailable())
        self.assertEqual(run_walkforward(**args), baseline)

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
