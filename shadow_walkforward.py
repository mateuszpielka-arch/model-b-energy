"""Deterministic offline SHADOW walk-forward and common-sample ranking.

Simulates issuance at each D-1 09:50; never creates real FROZEN forecasts.
No I/O. Verified canonical history is a prerequisite, not produced by this module.
"""
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, localcontext
from shadow_asof import select_snapshot, snapshot_identifier
from shadow_ols import predict_ols
from shadow_similar_consensus import predict_similar, median_consensus
from shadow_scoring import _utc, _prices, expected_periods, score_day

MODELS=('ols30','ols60','ols90','similar_shape','similar_level','median4')


def run_walkforward(*,history,target_days,evaluated_at):
    """Require all six models on each scored day; rank on common UTC periods.

    Fixed preset: similar days 90D/k5 (shape and level), median of OLS30/60/90
    plus shape. No parameter search on evaluation data. Simulated cutoff is
    explicitly reported and must never be mistaken for a real issuance timestamp.
    """
    evaluated = _utc(evaluated_at)
    target_days = tuple(target_days)
    packets = {}
    for packet in history:
        day = packet['day']
        if day in packets:
            raise ValueError('Duplicate historical packet day')
        snapshots = packet.get('snapshots')
        if isinstance(snapshots, Iterator):
            packet = {**packet, 'snapshots': tuple(snapshots)}
        actual = packet.get('actual')
        if isinstance(actual, Iterator):
            packet = {**packet, 'actual': tuple(actual)}
        packets[day] = packet
    history = list(packets.values())
    accepted=[]; skipped=[]; totals={m:dict(n=0,nz=0,ae=Decimal(0),err=Decimal(0),
                                           denom=Decimal(0),ape=Decimal(0)) for m in MODELS}
    with localcontext() as ctx:
        ctx.prec=34
        for day in sorted(set(target_days)):
            stage='actual'
            try:
                if day not in packets: raise ValueError('Missing target packet')
                target=packets[day]
                if target.get('actual_verified') is not True or not target.get('actual_version'):
                    raise ValueError('Unverified target actual')
                actual_available = _utc(target.get('actual_available_at'))
                if actual_available > evaluated:
                    raise ValueError('Target actual not yet available for evaluation')
                selected = select_snapshot(day, target.get('snapshots', ()))
                if evaluated < selected.cutoff_utc:
                    raise ValueError('Simulated issuance is after evaluation time')
                if actual_available <= selected.cutoff_utc:
                    raise ValueError('Target actual timestamp precedes simulated issuance or coincides with it')
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
                    models={m:predictions[m] for m in ('ols30','ols60','ols90','similar_shape')})
                stage='scoring'
                snapshot=next(s for s in target['snapshots'] if snapshot_identifier(s)==selected.snapshot_id)
                publication=max(_utc(r['publication_ts']) for r in snapshot['rows'])
                retrieved=_utc(snapshot['retrieval_completed_at'])
                scores={}
                for name in MODELS:
                    scores[name]=score_day(day=day,forecast=predictions[name]['prices'],actual=actual.items(),
                        forecast_id=f'simulated:{day}:{name}',model_version=predictions[name]['model_version'],
                        actual_version=target['actual_version'],forecast_status='SHADOW',actual_verified=True,
                        issued_at=selected.cutoff_utc,publication_ts=publication,retrieved_at=retrieved)
            except ValueError as exc:
                skipped.append(dict(day=day.isoformat(),stage=stage,reason=str(exc)));continue
            # Accumulate only after ALL models and scores have passed for the day.
            for name in MODELS:
                predicted=_prices(predictions[name]['prices']); t=totals[name]
                for p,y in actual.items():
                    error=predicted[p]-y;t['n']+=1;t['ae']+=abs(error);t['err']+=error;t['denom']+=abs(y)
                    if y!=0:t['nz']+=1;t['ape']+=abs(error)/abs(y)
            accepted.append(dict(day=day.isoformat(),periods=len(actual),
                period_starts_utc=[period.isoformat() for period in sorted(actual)],
                target_snapshot_id=selected.snapshot_id,
                actual_version=target["actual_version"],
                actual_available_at=actual_available.isoformat(),
                target_latest_publication_ts=publication.isoformat(),
                target_retrieval_started_at=_utc(snapshot["retrieved_at"]).isoformat(),
                target_retrieval_completed_at=retrieved.isoformat(),
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
    return dict(skipped_days_by_stage=skipped_days_by_stage,simulation=True,evaluated_at=evaluated.isoformat(),requested_days=len(set(target_days)),
        common_days=len(accepted),common_periods=sum(day["periods"] for day in accepted),accepted=accepted,skipped=skipped,ranking=ranking)
