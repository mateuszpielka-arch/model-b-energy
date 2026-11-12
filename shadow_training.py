"""Shared strict training admission for JWCD SHADOW models; no I/O."""
from datetime import timedelta
from shadow_asof import cutoff_for_day, select_snapshot
from shadow_scoring import _utc,_prices,expected_periods

class IncompleteTrainingWindow(ValueError):
    def __init__(self, missing_days):
        self.missing_days = tuple(sorted(missing_days))
        super().__init__(f'Incomplete calendar training window: first missing day {self.missing_days[0]}')


class LateTrainingActuals(ValueError):
    def __init__(self, days):
        self.days = tuple(days)
        super().__init__(f'Historical actual unavailable at target cutoff: {self.days[0]}')


def has_verified_actual(packet):
    version = packet.get('actual_version')
    return packet.get('actual_verified') is True and isinstance(version, str) and bool(version.strip())


def prepare_training(*,target_day,target_snapshots,history,window_days):
    if window_days not in (30,60,90):
        raise ValueError('Only JWCD OLS 30/60/90 calendar-day windows allowed')
    cutoff = cutoff_for_day(target_day)
    required={target_day-timedelta(days=i) for i in range(1,window_days+1)}
    packets={}
    for packet in history:
        d=packet['day']
        if d not in required: continue  # Future or outside-window records cannot affect fit.
        if d in packets: raise ValueError('Duplicate historical delivery date')
        packets[d]=packet
    missing = required - packets.keys()
    if missing:
        raise IncompleteTrainingWindow(missing)
    target=select_snapshot(target_day,target_snapshots)
    if target.snapshot_id is None: raise ValueError('No complete pre-cutoff target JWCD')
    available_at = {}
    late_days = []
    for day, p in sorted(packets.items()):
        if not has_verified_actual(p):
            raise ValueError(f'Historical actual provenance not verified: {day}')
        actual_available = _utc(p.get('actual_available_at'))
        if actual_available <= cutoff_for_day(day):
            raise ValueError(f'Historical actual available at or before its simulated issuance: {day}')
        if actual_available > cutoff:
            late_days.append(day)
        available_at[day] = actual_available
    if late_days:
        raise LateTrainingActuals(late_days)

    prepared=[]; evidence=[]; timing=[]
    for day, actual_available in available_at.items():
        p = packets[day]
        selection=select_snapshot(day, p.get('snapshots', ()))
        if selection.snapshot_id is None:
            reason = f'Historical JWCD availability not proven: {day}'
            if selection.rejected:
                details = ', '.join(sorted({code for _, code in selection.rejected}))
                reason += f' ({details})'
            raise ValueError(reason)
        actual=_prices(p.get('actual', ()))
        if set(actual)!=set(expected_periods(day)):
            raise ValueError(f'Historical actual incomplete or invalid periods: {day}')
        prepared.append((day,selection.rows,actual))
        evidence.append((day.isoformat(),selection.snapshot_id,p['actual_version']))
        timing.append(dict(
            day=day.isoformat(),
            snapshot_id=selection.snapshot_id,
            actual_version=p['actual_version'],
            jwcd_cutoff_utc=selection.cutoff_utc.isoformat(),
            latest_publication_ts_utc=selection.latest_publication_ts_utc.isoformat(),
            retrieved_at_utc=selection.retrieved_at_utc.isoformat(),
            retrieval_completed_at_utc=selection.retrieval_completed_at_utc.isoformat(),
            actual_available_at_utc=actual_available.isoformat(),
        ))
    return cutoff,target,tuple(prepared),tuple(evidence),tuple(timing)
