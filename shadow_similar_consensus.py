"""Deterministic JWCD nearest days and fixed-member median; SHADOW only."""
from collections import defaultdict
from decimal import Decimal, localcontext
from shadow_training import prepare_training
from shadow_scoring import WARSAW, _prices, expected_periods


def _local_average(rows):
    grouped=defaultdict(list)
    for p,v in rows: grouped[p.astimezone(WARSAW).hour].append(v)
    return {h:sum(values,Decimal(0))/len(values) for h,values in grouped.items()}


def predict_similar(*, target_day, target_snapshots, history, window_days=90, k=5, mode='shape'):
    """Mean prices of k closest past JWCD profiles, equally weighted.

    Squared distance uses MW levels, or mean-centered MW for shape (no scaling).
    Compare only the target's local-hour buckets. Past days must cover all of
    them, and repeated DST hours are averaged.
    k/window/mode must be fixed before out-of-sample evaluation, never tuned here.
    """
    if mode not in ('shape','level') or isinstance(k,bool) or not isinstance(k,int) or k<1:
        raise ValueError('Fixed valid mode and positive integer k required')
    cutoff,target,prepared,evidence,timing=prepare_training(target_day=target_day,
        target_snapshots=target_snapshots,history=history,window_days=window_days)
    with localcontext() as ctx:
        ctx.prec=34
        tx=_local_average(target.rows); hours=sorted(tx); candidates=[]; excluded=[]
        tmean=sum(tx.values())/len(tx) if mode=='shape' else Decimal(0)
        for day,xrows,actual in prepared:
            x=_local_average(xrows); y=_local_average(actual.items())
            if not tx.keys() <= x.keys() or not tx.keys() <= y.keys():
                excluded.append((day.isoformat(),'DIFFERENT_LOCAL_HOUR_SET')); continue
            mean=sum(x[h] for h in hours)/len(hours) if mode=='shape' else Decimal(0)
            distance=sum(((x[h]-mean)-(tx[h]-tmean))**2 for h in hours)/len(hours)
            candidates.append((distance,day,y))
        if len(candidates)<k: raise ValueError('Not enough structurally comparable similar days')
        # Tie: most recent past date first, independent of input order.
        chosen=sorted(candidates,key=lambda r:(r[0],-r[1].toordinal()))[:k]
        hourly={h:sum((y[h] for _,_,y in chosen),Decimal(0))/k for h in hours}
        predictions=tuple((p,hourly[p.astimezone(WARSAW).hour]) for p,_ in target.rows)
    return dict(model_version=f'jwcd-similar-{mode}-{window_days}d-k{k}-shadow-v2',
        target_day=target_day.isoformat(),target_snapshot_id=target.snapshot_id,
        cutoff_utc=cutoff.isoformat(),prices=predictions,training_evidence=evidence,training_timing=timing,
        neighbors=tuple((d.isoformat(),dist) for dist,d,_ in chosen),excluded=tuple(excluded))


MEDIAN_MEMBERS=('ols30','ols60','ols90','similar_shape')

def median_consensus(*,target_day,models):
    """All four fixed members required; no partial-consensus fallback."""
    if set(models)!=set(MEDIAN_MEMBERS): raise ValueError('Exactly four fixed median members required')
    snapshots={m.get('target_snapshot_id') for m in models.values()}
    if len(snapshots)!=1 or None in snapshots: raise ValueError('Target snapshots differ or missing')
    expected=set(expected_periods(target_day)); member_prices=[]
    for name in MEDIAN_MEMBERS:
        m=models[name]
        if m.get('target_day')!=target_day.isoformat() or not m.get('model_version'):
            raise ValueError('Member day/version mismatch')
        prices=_prices(m['prices'])
        if set(prices)!=expected: raise ValueError('Incomplete median member periods')
        member_prices.append(prices)
    with localcontext() as ctx:
        ctx.prec=34
        rows=[]
        for p in sorted(expected):
            values=sorted(v[p] for v in member_prices)
            rows.append((p,(values[1]+values[2])/2))
    return dict(model_version='jwcd-fixed4-median-shadow-v2',target_day=target_day.isoformat(),
        target_snapshot_id=next(iter(snapshots)),prices=tuple(rows),
        members=tuple((name,models[name]['model_version']) for name in MEDIAN_MEMBERS))
