"""Shared strict training admission for JWCD SHADOW models; no I/O."""
from datetime import datetime,time,timedelta
from shadow_asof import select_snapshot
from shadow_scoring import WARSAW,_utc,_prices,expected_periods

def prepare_training(*,target_day,target_snapshots,history,window_days):
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
    prepared=[]; evidence=[]
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
        prepared.append((day,selection.rows,actual))
        evidence.append((day.isoformat(),selection.snapshot_id,p['actual_version']))
    return cutoff,target,tuple(prepared),tuple(evidence)
