"""Pure hourly JWCD OLS SHADOW, with exact calendar windows and as-of gates.

No production imports, files, network, DB, clipping, or automatic fallbacks.
Training packets require independently verified actuals; caller owns provenance.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext
from shadow_asof import select_snapshot
from shadow_scoring import WARSAW, _utc, _prices, expected_periods


def predict_ols(*, target_day, target_snapshots, history, window_days):
    """Fit each Warsaw local-hour bucket independently on the prior 30/60/90 days.

    history packets: day, snapshots (canonical JWCD candidates), actual (UTC price
    pairs), actual_verified=True, actual_version, actual_available_at (aware).
    Historical JWCD must have existed by its OWN D-1 09:50. Actual must have
    existed by the target's D-1 09:50. Missing a calendar day blocks the fit.
    DST repeated local hours both contribute observations; target repeats share
    the same coefficient. This is an explicit SHADOW policy, not the production
    hour/sequence mapping or a claim that official PSE/TGE DST labels are resolved.
    """
    if window_days not in (30,60,90):
        raise ValueError('Only JWCD OLS 30/60/90 calendar-day windows allowed')
    cutoff=_utc(datetime.combine(target_day-timedelta(days=1),time(9,50),WARSAW))
    required={target_day-timedelta(days=i) for i in range(1,window_days+1)}
    packets={}
    for packet in history:
        d=packet['day']
        if d not in required: continue  # Future or outside-window records cannot affect fit.
        if d in packets: raise ValueError('Duplicate historical delivery date')
        packets[d]=packet
    if set(packets)!=required:
        raise ValueError('Incomplete calendar training window')
    target=select_snapshot(target_day,target_snapshots)
    if target.snapshot_id is None: raise ValueError('No complete pre-cutoff target JWCD')
    buckets=defaultdict(list); evidence=[]
    for day,p in sorted(packets.items()):
        if p.get('actual_verified') is not True or not p.get('actual_version'):
            raise ValueError('Historical actual provenance not verified')
        if _utc(p.get('actual_available_at'))>cutoff:
            raise ValueError('Historical actual unavailable at target cutoff')
        selection=select_snapshot(day,p['snapshots'])
        if selection.snapshot_id is None: raise ValueError('Historical JWCD availability not proven')
        actual=_prices(p['actual'])
        if set(actual)!=set(expected_periods(day)):
            raise ValueError('Historical actual incomplete or invalid periods')
        for period,jwcd in selection.rows:
            buckets[period.astimezone(WARSAW).hour].append((jwcd,actual[period]))
        evidence.append((day.isoformat(),selection.snapshot_id,p['actual_version']))
    coefficients={}
    with localcontext() as ctx:
        ctx.prec=34
        for hour in range(24):
            values=buckets[hour]; n=len(values)
            if n<2: raise ValueError('Insufficient observations in hourly bucket')
            mx=sum((x for x,y in values),Decimal(0))/n
            my=sum((y for x,y in values),Decimal(0))/n
            variance=sum(((x-mx)**2 for x,y in values),Decimal(0))
            if variance==0: raise ValueError('Constant JWCD: OLS slope is unidentified')
            slope=sum(((x-mx)*(y-my) for x,y in values),Decimal(0))/variance
            coefficients[hour]=dict(intercept=my-slope*mx,slope=slope,observations=n)
        predictions=[]
        for period,x in target.rows:
            c=coefficients[period.astimezone(WARSAW).hour]
            predictions.append((period,c['intercept']+c['slope']*x))
    return dict(model_version=f'jwcd-ols-{window_days}d-local-hour-shadow-v1',
        target_day=target_day.isoformat(),target_snapshot_id=target.snapshot_id,
        cutoff_utc=cutoff.isoformat(),window_days=window_days,
        training_evidence=tuple(evidence),coefficients=coefficients,prices=tuple(predictions))
