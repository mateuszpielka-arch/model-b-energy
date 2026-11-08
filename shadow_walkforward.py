"""Deterministic offline SHADOW walk-forward and common-sample ranking.

Simulates issuance at each D-1 09:50; never creates real FROZEN forecasts.
No I/O. Verified canonical history is a prerequisite, not produced by this module.
"""
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from shadow_asof import cutoff_for_day, preserve_snapshot_rows, select_snapshot
from shadow_ols import predict_ols
from shadow_similar_consensus import MEDIAN_MEMBERS, median_consensus, predict_similar
from shadow_scoring import _utc, _prices, expected_periods, score_day

MODELS=('ols30','ols60','ols90','similar_shape','similar_level','median4')


def run_walkforward(*,history,target_days,evaluated_at):
    """Require all six models on each scored day; rank on common UTC periods.

    Fixed preset: similar days 90D/k5 (shape and level), median of OLS30/60/90
    plus shape. No parameter search on evaluation data. Simulated cutoff is
    explicitly reported and must never be mistaken for a real issuance timestamp.
    Only requested delivery dates and their preceding 90-day windows are read.
    """
    evaluated = _utc(evaluated_at)
    target_days = sorted(set(target_days))
    max_window = timedelta(days=90)
    packets = {}
    unreadable_packets = {}
    for packet in history:
        day = packet['day']
        next_target = bisect_left(target_days, day)
        if next_target == len(target_days) or target_days[next_target] - day > max_window:
            continue
        if day in packets or day in unreadable_packets:
            raise ValueError('Duplicate historical packet day')
        try:
            actual_available = _utc(packet.get('actual_available_at'))
        except ValueError:
            actual_available = None
        if actual_available is None or not cutoff_for_day(day) < actual_available <= evaluated:
            # Keep the packet for cutoff diagnostics without consuming its payload.
            packets[day] = packet
            continue
        for field in ('snapshots', 'actual'):
            values = packet.get(field)
            if not isinstance(values, Iterator):
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
    accepted=[]; skipped=[]; totals={m:dict(n=0,nz=0,ae=Decimal(0),err=Decimal(0),
                                           denom=Decimal(0),ape=Decimal(0)) for m in MODELS}
    with localcontext() as ctx:
        ctx.prec=34
        for day in target_days:
            stage='actual'
            scoring_model = None
            try:
                if day in unreadable_packets:
                    stage = 'packet'
                    raise ValueError(f'Unreadable target packet field: {unreadable_packets[day]}')
                if day not in packets: raise ValueError('Missing target packet')
                target=packets[day]
                if target.get('actual_verified') is not True or not target.get('actual_version'):
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
                for n in (30,60,90):
                    stage=f'ols{n}'; predictions[stage]=predict_ols(**common,window_days=n)
                for mode in ('shape','level'):
                    stage='similar_'+mode
                    predictions[stage]=predict_similar(**common,window_days=90,k=5,mode=mode)
                stage='median4'; predictions[stage]=median_consensus(target_day=day,
                    models={m:predictions[m] for m in MEDIAN_MEMBERS})
                stage='scoring'
                publication = selected.latest_publication_ts_utc
                retrieved = selected.retrieval_completed_at_utc
                input_times = {}
                for name in MODELS[:-1]:
                    scoring_model = name
                    latest_publication, latest_retrieval = publication, retrieved
                    training_timing = predictions[name]['training_timing']
                    latest_training_actual = max(
                        _utc(datetime.fromisoformat(evidence['actual_available_at_utc']))
                        for evidence in training_timing
                    )
                    for evidence in training_timing:
                        latest_publication = max(latest_publication,
                            datetime.fromisoformat(evidence['latest_publication_ts_utc']))
                        latest_retrieval = max(latest_retrieval,
                            datetime.fromisoformat(evidence['retrieval_completed_at_utc']))
                    input_times[name] = (latest_publication, latest_retrieval, latest_training_actual)
                scoring_model = 'median4'
                input_times['median4'] = (
                    max(input_times[name][0] for name in MEDIAN_MEMBERS),
                    max(input_times[name][1] for name in MEDIAN_MEMBERS),
                    max(input_times[name][2] for name in MEDIAN_MEMBERS),
                )
                scores={}
                for name, (latest_publication, latest_retrieval, latest_training_actual) in input_times.items():
                    scoring_model = name
                    if latest_training_actual > selected.cutoff_utc:
                        raise ValueError('Training Fix1 prices unavailable at D-1 09:50')
                    scores[name]=score_day(day=day,forecast=predictions[name]['prices'],actual=actual.items(),
                        forecast_id=f'simulated:{day}:{name}',model_version=predictions[name]['model_version'],
                        actual_version=target['actual_version'],forecast_status='SHADOW',actual_verified=True,
                        issued_at=selected.cutoff_utc,publication_ts=latest_publication,retrieved_at=latest_retrieval)
            except ValueError as exc:
                rejection = dict(day=day.isoformat(), stage=stage, reason=str(exc))
                if scoring_model is not None:
                    rejection['model'] = scoring_model
                skipped.append(rejection)
                continue
            # Accumulate only after ALL models and scores have passed for the day.
            periods = sorted(actual)
            for name in MODELS:
                predicted=_prices(predictions[name]['prices']); t=totals[name]
                for p in periods:
                    y = actual[p]
                    error=predicted[p]-y;t['n']+=1;t['ae']+=abs(error);t['err']+=error;t['denom']+=abs(y)
                    if y!=0:t['nz']+=1;t['ape']+=abs(error)/abs(y)
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
        ranking=[]
        if accepted:
            for name,t in totals.items():
                ranking.append(dict(model=name,days=len(accepted),periods=t['n'],mape_periods=t['nz'],
                    zero_actual_periods=t['n']-t['nz'],mae=t['ae']/t['n'],bias=t['err']/t['n'],
                    mape_pct=100*t['ape']/t['nz'] if t['nz'] else None,
                    wape_pct=100*t['ae']/t['denom'] if t['denom'] else None))
            ranking.sort(key=lambda r:(r['mae'],r['model']))
            for row in ranking:row['rank']=1+sum(other['mae']<row['mae'] for other in ranking)
    skipped_days_by_stage = {}
    for entry in skipped:
        skipped_days_by_stage.setdefault(entry['stage'], []).append(entry['day'])
    common_days_by_hours = Counter(day["periods"] for day in accepted)
    return dict(skipped_days_by_stage=skipped_days_by_stage,simulation=True,evaluated_at=evaluated.isoformat(),requested_days=len(target_days),
        common_days=len(accepted),
        common_days_by_hours={str(hours): common_days_by_hours[hours] for hours in (23, 24, 25)},
        common_periods=sum(day["periods"] for day in accepted),accepted=accepted,skipped=skipped,
        unreadable_packets={day.isoformat(): unreadable_packets[day] for day in sorted(unreadable_packets)},
        ranking_metric="mae", ranking=ranking)
