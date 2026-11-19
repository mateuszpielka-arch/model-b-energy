"""Deterministic offline SHADOW walk-forward and common-sample ranking.

Simulates issuance at each D-1 09:50; never creates real FROZEN forecasts.
No I/O. Verified canonical history is a prerequisite, not produced by this module.
"""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
from shadow_asof import cutoff_for_day, preserve_snapshot_rows, select_snapshot
from shadow_ols import predict_ols
from shadow_training import (
    IncompleteTrainingWindow, LateTrainingActuals, UnavailableTrainingJWCD, has_verified_actual,
)
from shadow_similar_consensus import MEDIAN_MEMBERS, median_consensus, predict_similar
from shadow_scoring import (
    WARSAW, _SHADOW_DECIMAL_CONTEXT, _prices, _utc, expected_periods, score_day,
    validate_forecast_identity, validate_forecast_timing,
)

OLS_WINDOWS = (30, 60, 90)
SIMILAR_WINDOW = 90
TRAINING_WINDOWS = {
    **{f'ols{window}': window for window in OLS_WINDOWS},
    'similar_shape': SIMILAR_WINDOW,
    'similar_level': SIMILAR_WINDOW,
}
MODELS = (*TRAINING_WINDOWS, 'median4')
FROZEN_90D_VERSION = 'jwcd-linear-hourly-90d-v1-20261006'


def _empty_totals(models=MODELS):
    return {
        model: dict(n=0, nz=0, ae=Decimal(0), err=Decimal(0), denom=Decimal(0), ape=Decimal(0))
        for model in models
    }


def _accumulate(totals, error, actual):
    absolute_error = abs(error)
    absolute_actual = abs(actual)
    for total in totals:
        total['n'] += 1
        total['ae'] += absolute_error
        total['err'] += error
        total['denom'] += absolute_actual
        if absolute_actual:
            total['nz'] += 1
            total['ape'] += absolute_error / absolute_actual


def _stage_common_day(*, day, forecast_prices, actual, totals, totals_by_hours,
                      totals_by_day_type, totals_by_local_hour):
    """Accumulate a whole common day on detached sums before committing any."""
    staged = deepcopy((totals, totals_by_hours, totals_by_day_type, totals_by_local_hour))
    overall, by_hours, by_day_type, by_local_hour = staged
    periods = sorted(actual)
    day_type = 'weekend' if day.weekday() >= 5 else 'weekday'
    hourly_totals = {}
    local_hours = []
    for period in periods:
        local = period.astimezone(WARSAW)
        hour = local.strftime('%H:%M')
        if local.fold:
            hour += '[fold=1]'
        hourly_totals[period] = by_local_hour[hour]
        local_hours.append(hour)
    for name in MODELS:
        predicted = forecast_prices[name]
        model_totals = (overall[name], by_hours[len(periods)][name], by_day_type[day_type][name])
        for period in periods:
            value = actual[period]
            _accumulate((*model_totals, hourly_totals[period][name]), predicted[period] - value, value)
    return (*staged, local_hours)


def _ranking_percentage(numerator, denominator):
    # Multiplication by 100 needs two extra exponent positions, not extra precision.
    # Apply the original context to the final result so real range failures remain errors.
    with localcontext() as context:
        context.Emax += 2
        result = 100 * numerator / denominator
    return +result


def _rank(totals, days):
    if not days:
        return []
    ranking = [
        dict(model=name, days=days, periods=t['n'], mape_periods=t['nz'],
             zero_actual_periods=t['n'] - t['nz'], mae=t['ae'] / t['n'], bias=t['err'] / t['n'],
             mape_pct=_ranking_percentage(t['ape'], t['nz']) if t['nz'] else None,
             wape_pct=_ranking_percentage(t['ae'], t['denom']) if t['denom'] else None)
        for name, t in totals.items()
    ]
    ranking.sort(key=lambda row: (row['mae'], row['model']))
    for row in ranking:
        row['rank'] = 1 + sum(other['mae'] < row['mae'] for other in ranking)
    return ranking


def run_walkforward(*,history,target_days,evaluated_at,frozen_90d_by_day=None):
    """Require all six models on each scored day; rank on common UTC periods.

    Fixed preset: similar days 90D/k5 (shape and level), median of OLS30/60/90
    plus shape. No parameter search on evaluation data. Simulated cutoff is
    explicitly reported and must never be mistaken for a real issuance timestamp.
    Only requested delivery dates and their preceding 90-day windows are read.
    Optional archived FROZEN records are keyed by delivery date and scored
    separately against the same actuals as OLS90, with caller-supplied provenance.
    """
    evaluated = _utc(evaluated_at)
    target_days = sorted(set(target_days))
    max_window_days = max(TRAINING_WINDOWS.values())
    requested_days_by_hours = Counter(len(expected_periods(day)) for day in target_days)
    requested_periods = sum(hours * count for hours, count in requested_days_by_hours.items())
    target_ordinals = [day.toordinal() for day in target_days]
    elapsed_targets = bisect_right(target_days, evaluated, key=cutoff_for_day)
    packets = {}
    unreadable_packets = {}
    for packet in history:
        day = packet['day']
        ordinal = day.toordinal()
        next_target = bisect_left(target_ordinals, ordinal)
        if next_target == len(target_days) or target_ordinals[next_target] - ordinal > max_window_days:
            continue
        if day in packets or day in unreadable_packets:
            raise ValueError('Duplicate historical packet day')
        try:
            actual_available = _utc(packet.get('actual_available_at'))
        except ValueError:
            actual_available = None
        if (not has_verified_actual(packet) or actual_available is None
                or not cutoff_for_day(day) < actual_available <= evaluated):
            # Keep the packet for diagnostics without consuming its payload.
            packets[day] = packet
            continue
        if target_ordinals[next_target] != ordinal:
            last_target = bisect_right(
                target_ordinals, ordinal + max_window_days, hi=elapsed_targets,
            ) - 1
            if last_target < next_target or actual_available > cutoff_for_day(target_days[last_target]):
                # No forecast in this window could use the packet at its simulated issuance.
                packets[day] = packet
                continue
        for field in ('snapshots', 'actual'):
            values = packet.get(field)
            if not isinstance(values, Iterable) or isinstance(values, (list, tuple, str, bytes, Mapping)):
                continue
            try:
                packet = {**packet, field: tuple(values)}
            except (ValueError, TypeError, RuntimeError, OSError):
                unreadable_packets[day] = field
                break
        else:
            if 'snapshots' in packet:
                packet = {**packet, 'snapshots': preserve_snapshot_rows(packet['snapshots'])}
            packets[day] = packet
    history = list(packets.values())
    accepted = []
    skipped = []
    benchmark = dict(paired_days=[], excluded_days={})
    benchmark_totals = _empty_totals(('frozen_90d', 'ols90'))
    totals = _empty_totals()
    totals_by_hours = {hours: _empty_totals() for hours in (23, 24, 25)}
    totals_by_day_type = {kind: _empty_totals() for kind in ('weekday', 'weekend')}
    common_days_by_day_type = Counter()
    totals_by_local_hour = defaultdict(_empty_totals)
    common_periods_by_local_hour = Counter()
    with localcontext(_SHADOW_DECIMAL_CONTEXT):
        for day in target_days:
            stage='actual'
            scoring_model = None
            prediction_period_coverage = None
            missing_training_timing_days = None
            try:
                if day in unreadable_packets:
                    stage = 'packet'
                    raise ValueError(f'Unreadable target packet field: {unreadable_packets[day]}')
                if day not in packets: raise ValueError('Missing target packet')
                target=packets[day]
                if not has_verified_actual(target):
                    raise ValueError('Unverified target actual')
                actual_available = _utc(target.get('actual_available_at'))
                if actual_available > evaluated:
                    raise ValueError('Target actual not yet available for evaluation')
                cutoff = cutoff_for_day(day)
                if evaluated < cutoff:
                    raise ValueError('Simulated issuance is after evaluation time')
                if actual_available <= cutoff:
                    raise ValueError('Target actual timestamp precedes simulated issuance or coincides with it')
                selected = select_snapshot(day, target.get('snapshots', ()))
                actual=_prices(target.get('actual', ()))
                if set(actual)!=set(expected_periods(day)): raise ValueError('Incomplete target actual periods')
                if selected.snapshot_id is None:
                    skipped.append(dict(
                        day=day.isoformat(), stage="target_snapshot",
                        reason="No eligible target JWCD snapshot",
                        rejected_snapshots=selected.rejected,
                    ))
                    continue
                common=dict(target_day=day,target_snapshots=target['snapshots'],history=history)
                predictions={}
                for n in OLS_WINDOWS:
                    stage=f'ols{n}'; predictions[stage]=predict_ols(**common,window_days=n)
                for mode in ('shape','level'):
                    stage='similar_'+mode
                    predictions[stage]=predict_similar(**common,window_days=SIMILAR_WINDOW,k=5,mode=mode)
                for name in MODELS[:-1]:
                    if not isinstance(predictions[name], Mapping):
                        stage = name
                        raise ValueError('Invalid model prediction object')
                forecast_prices = {}
                for name in MEDIAN_MEMBERS:
                    stage = name
                    try:
                        prices = _prices(predictions[name].get('prices'))
                    except (RuntimeError, OSError) as exc:
                        raise ValueError('Unreadable price collection') from exc
                    forecast_prices[name] = prices
                    predictions[name] = {**predictions[name], 'prices': tuple(prices.items())}
                stage='median4'; predictions[stage]=median_consensus(target_day=day,
                    models={m:predictions[m] for m in MEDIAN_MEMBERS})
                if not isinstance(predictions['median4'], Mapping):
                    raise ValueError('Invalid model prediction object')
                stage='scoring'
                for scoring_model in MODELS:
                    if predictions[scoring_model].get('target_day') != day.isoformat():
                        raise ValueError('Prediction delivery date does not match target delivery')
                    if predictions[scoring_model].get('target_snapshot_id') != selected.snapshot_id:
                        raise ValueError('Prediction does not use the selected target JWCD snapshot')
                expected = set(expected_periods(day))
                for scoring_model in MODELS:
                    validate_forecast_identity(
                        f'simulated:{day}:{scoring_model}',
                        predictions[scoring_model].get('model_version'), 'SHADOW',
                    )
                    if scoring_model not in forecast_prices:
                        try:
                            prices = _prices(predictions[scoring_model].get('prices'))
                        except (RuntimeError, OSError) as exc:
                            raise ValueError('Unreadable price collection') from exc
                        forecast_prices[scoring_model] = prices
                        predictions[scoring_model] = {
                            **predictions[scoring_model], 'prices': tuple(prices.items()),
                        }
                    observed = set(forecast_prices[scoring_model])
                    if observed != expected:
                        prediction_period_coverage = {
                            'missing_periods_utc': [period.isoformat() for period in sorted(expected - observed)],
                            'unexpected_periods_utc': [period.isoformat() for period in sorted(observed - expected)],
                        }
                        raise ValueError('Incomplete or mismatched delivery periods')
                publication = selected.latest_publication_ts_utc
                retrieved = selected.retrieval_completed_at_utc
                input_times = {}
                for name in MODELS[:-1]:
                    scoring_model = name
                    latest_publication, latest_retrieval = publication, retrieved
                    training_timing = predictions[name].get('training_timing')
                    if training_timing is None:
                        training_timing = ()
                    if not isinstance(training_timing, (tuple, list)):
                        raise ValueError('Invalid training timing evidence collection')
                    window_days = TRAINING_WINDOWS[name]
                    window_start = day - timedelta(days=window_days)
                    training_days = set()
                    training_actual_times = []
                    for evidence in training_timing:
                        try:
                            training_day = date.fromisoformat(evidence['day'])
                        except (KeyError, TypeError, ValueError) as exc:
                            raise ValueError('Invalid delivery date in training timing evidence') from exc
                        if training_day >= day:
                            raise ValueError(f'Training delivery must precede target delivery: {training_day}')
                        if training_day < window_start:
                            raise ValueError(f'Training timing evidence outside the {window_days}D window: {training_day}')
                        if training_day in training_days:
                            raise ValueError(f'Duplicate training timing evidence: {training_day}')
                        training_days.add(training_day)
                        training_cutoff = cutoff_for_day(training_day)
                        timestamps = {}
                        for field in (
                            'actual_available_at_utc', 'latest_publication_ts_utc',
                            'retrieved_at_utc', 'retrieval_completed_at_utc',
                        ):
                            try:
                                timestamps[field] = _utc(datetime.fromisoformat(evidence[field]))
                            except (KeyError, TypeError, ValueError) as exc:
                                raise ValueError(
                                    f'Invalid training timing evidence for {training_day}: {field}'
                                ) from exc
                        available = timestamps['actual_available_at_utc']
                        if available <= training_cutoff:
                            raise ValueError(f'Training Fix1 available at or before its own D-1 09:50: {training_day}')
                        if available > selected.cutoff_utc:
                            raise ValueError('Training Fix1 prices unavailable at D-1 09:50')
                        training_actual_times.append(available)
                        published = timestamps['latest_publication_ts_utc']
                        started = timestamps['retrieved_at_utc']
                        completed = timestamps['retrieval_completed_at_utc']
                        if max(published, started) > completed or completed > training_cutoff:
                            raise ValueError(f'Training JWCD chronology invalid at D-1 09:50: {training_day}')
                        latest_publication = max(latest_publication, published)
                        latest_retrieval = max(latest_retrieval, completed)
                    if len(training_days) != window_days:
                        missing_training_timing_days = [
                            (window_start + timedelta(days=offset)).isoformat()
                            for offset in range(window_days)
                            if window_start + timedelta(days=offset) not in training_days
                        ]
                        raise ValueError(f'Incomplete training timing evidence for the {window_days}D window')
                    input_times[name] = (latest_publication, latest_retrieval, max(training_actual_times))
                scoring_model = 'median4'
                input_times['median4'] = (
                    max(input_times[name][0] for name in MEDIAN_MEMBERS),
                    max(input_times[name][1] for name in MEDIAN_MEMBERS),
                    max(input_times[name][2] for name in MEDIAN_MEMBERS),
                )
                scores = {}
                for name, (latest_publication, latest_retrieval, latest_training_actual) in input_times.items():
                    scoring_model = name
                    forecast = forecast_prices[name]
                    scores[name]=score_day(day=day,forecast=forecast.items(),actual=actual.items(),
                        forecast_id=f'simulated:{day}:{name}',model_version=predictions[name]['model_version'],
                        actual_version=target['actual_version'],forecast_status='SHADOW',actual_verified=True,
                        issued_at=selected.cutoff_utc,publication_ts=latest_publication,retrieved_at=latest_retrieval)
            except (ValueError, DecimalException) as exc:
                numeric_error = type(exc).__name__ if isinstance(exc, DecimalException) else None
                reason = f'Numeric computation failed: {numeric_error}' if numeric_error else str(exc)
                rejection = dict(day=day.isoformat(), stage=stage, reason=reason)
                if numeric_error:
                    rejection['numeric_error'] = numeric_error
                if missing_training_timing_days is not None:
                    rejection['missing_training_timing_days'] = missing_training_timing_days
                if prediction_period_coverage is not None:
                    rejection['prediction_period_coverage'] = prediction_period_coverage
                if isinstance(exc, IncompleteTrainingWindow):
                    missing_days = [missing for missing in exc.missing_days
                                    if missing not in unreadable_packets]
                    unreadable_days = {missing.isoformat(): unreadable_packets[missing]
                                       for missing in exc.missing_days if missing in unreadable_packets}
                    if missing_days:
                        rejection['missing_training_days'] = [missing.isoformat() for missing in missing_days]
                    if unreadable_days:
                        rejection['unreadable_training_days'] = unreadable_days
                        details = ', '.join(f'{date_label} ({field})'
                                            for date_label, field in unreadable_days.items())
                        rejection['reason'] = f'Unreadable training packet fields: {details}'
                        if missing_days:
                            rejection['reason'] += f'. First missing day: {missing_days[0]}'
                if isinstance(exc, LateTrainingActuals):
                    rejection['late_training_actual_days'] = [day.isoformat() for day in exc.days]
                if isinstance(exc, UnavailableTrainingJWCD):
                    rejection['training_jwcd_rejections'] = {
                        day.isoformat(): reasons for day, reasons in exc.rejected_by_day.items()
                    }
                if scoring_model is not None:
                    rejection['model'] = scoring_model
                skipped.append(rejection)
                continue
            periods = sorted(actual)
            try:
                staged_totals = _stage_common_day(
                    day=day, forecast_prices=forecast_prices, actual=actual, totals=totals,
                    totals_by_hours=totals_by_hours, totals_by_day_type=totals_by_day_type,
                    totals_by_local_hour=totals_by_local_hour,
                )
            except DecimalException as exc:
                skipped.append(dict(
                    day=day.isoformat(), stage='aggregation',
                    reason=f'Numeric computation failed: {type(exc).__name__}',
                    numeric_error=type(exc).__name__,
                ))
                continue
            if frozen_90d_by_day is not None:
                label = day.isoformat()
                record = frozen_90d_by_day.get(day)
                try:
                    if record is None:
                        raise ValueError('No archived FROZEN forecast for delivery day')
                    if not isinstance(record, Mapping):
                        raise ValueError('Invalid archived FROZEN forecast record')
                    if (record.get('forecast_status') != 'FROZEN'
                            or record.get('model_version') != FROZEN_90D_VERSION):
                        raise ValueError('Expected archived FROZEN JWCD 90D model version')
                    validate_forecast_timing(day, record.get('issued_at'),
                                             record.get('publication_ts'), record.get('retrieved_at'))
                    try:
                        frozen_prices = _prices(record.get('prices'))
                    except (OSError, RuntimeError) as exc:
                        raise ValueError('Unreadable archived FROZEN forecast prices') from exc
                    frozen_score = score_day(
                        day=day, forecast=frozen_prices.items(), actual=actual.items(),
                        forecast_id=record.get('forecast_id'), model_version=record['model_version'],
                        actual_version=target['actual_version'], forecast_status='FROZEN',
                        actual_verified=True, issued_at=record.get('issued_at'),
                        publication_ts=record.get('publication_ts'), retrieved_at=record.get('retrieved_at'),
                    )
                    staged_benchmark_totals = deepcopy(benchmark_totals)
                    for name, prices in (('frozen_90d', frozen_prices), ('ols90', forecast_prices['ols90'])):
                        for period in periods:
                            value = actual[period]
                            _accumulate((staged_benchmark_totals[name],), prices[period] - value, value)
                except (ValueError, DecimalException) as exc:
                    reason = (f'Numeric computation failed: {type(exc).__name__}'
                              if isinstance(exc, DecimalException) else str(exc))
                    benchmark['excluded_days'][label] = reason
                else:
                    benchmark_totals = staged_benchmark_totals
                    benchmark['paired_days'].append(dict(day=label, frozen_90d=frozen_score, ols90=scores['ols90']))
            # Publish sums and counters only after the entire day has accumulated.
            (totals, totals_by_hours, totals_by_day_type, totals_by_local_hour,
             local_hours) = staged_totals
            day_type = 'weekend' if day.weekday() >= 5 else 'weekday'
            common_days_by_day_type[day_type] += 1
            common_periods_by_local_hour.update(local_hours)
            accepted.append(dict(day=day.isoformat(),periods=len(actual),
                period_starts_utc=[period.isoformat() for period in periods],
                target_snapshot_id=selected.snapshot_id,
                actual_version=target["actual_version"],
                actual_available_at=actual_available.isoformat(),
                target_latest_publication_ts=publication.isoformat(),
                target_retrieval_started_at=selected.retrieved_at_utc.isoformat(),
                target_retrieval_completed_at=retrieved.isoformat(),
                scoring_input_timing={
                    name: dict(latest_publication_ts_utc=published.isoformat(),
                               latest_retrieval_completed_at_utc=completed.isoformat(),
                               latest_training_actual_available_at_utc=training_actual.isoformat())
                    for name, (published, completed, training_actual) in input_times.items()
                },
                rejected_snapshots=selected.rejected,
                simulated_issue_at=selected.cutoff_utc.isoformat(),predictions=predictions,scores=scores))
        common_days_by_hours = Counter(day['periods'] for day in accepted)
        ranking = _rank(totals, len(accepted))
        if frozen_90d_by_day is not None:
            benchmark['ranking'] = _rank(benchmark_totals, len(benchmark['paired_days']))
            paired_days_by_hours = Counter(pair['frozen_90d'].periods for pair in benchmark['paired_days'])
            benchmark['coverage_by_hours'] = {
                str(hours): {
                    'shadow_days': common_days_by_hours[hours],
                    'paired_days': paired_days_by_hours[hours],
                    'unpaired_days': common_days_by_hours[hours] - paired_days_by_hours[hours],
                    'paired_periods': hours * paired_days_by_hours[hours],
                }
                for hours in (23, 24, 25)
            }
        ranking_by_hours = {
            str(hours): _rank(group, common_days_by_hours[hours])
            for hours, group in totals_by_hours.items()
        }
        ranking_by_day_type = {
            kind: _rank(group, common_days_by_day_type[kind])
            for kind, group in totals_by_day_type.items()
        }
        # Each local hour and fold occurs at most once per accepted delivery day.
        ranking_by_local_hour = {
            hour: _rank(totals_by_local_hour[hour], common_periods_by_local_hour[hour])
            for hour in sorted(totals_by_local_hour)
        }
    skipped_days_by_stage = {}
    for entry in skipped:
        skipped_days_by_stage.setdefault(entry['stage'], []).append(entry['day'])
    report = dict(skipped_days_by_stage=skipped_days_by_stage,simulation=True,evaluated_at=evaluated.isoformat(),requested_days=len(target_days),
        common_days=len(accepted), requested_periods=requested_periods,
        excluded_periods=requested_periods - sum(day["periods"] for day in accepted),
        coverage_by_hours={
            str(hours): dict(
                requested_days=requested_days_by_hours[hours],
                common_days=common_days_by_hours[hours],
                excluded_days=requested_days_by_hours[hours] - common_days_by_hours[hours],
                requested_periods=hours * requested_days_by_hours[hours],
                common_periods=hours * common_days_by_hours[hours],
                excluded_periods=hours * (requested_days_by_hours[hours] - common_days_by_hours[hours]),
            )
            for hours in (23, 24, 25)
        },
        common_days_by_hours={str(hours): common_days_by_hours[hours] for hours in (23, 24, 25)},
        common_periods=sum(day["periods"] for day in accepted),accepted=accepted,skipped=skipped,
        unreadable_packets={day.isoformat(): unreadable_packets[day] for day in sorted(unreadable_packets)},
        ranking_metric="mae", ranking=ranking, ranking_by_hours=ranking_by_hours,
        ranking_by_day_type=ranking_by_day_type, ranking_by_local_hour=ranking_by_local_hour)
    if frozen_90d_by_day is not None:
        report['frozen_90d_benchmark'] = benchmark
    return report
