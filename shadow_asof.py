"""Read-only availability gate for canonical JWCD snapshots; no DB/network I/O.

Legacy PSE payloads need a separately verified adapter for local/DST period labels.
retrieval_completed_at must be evidenced, never inferred from a request start time.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from shadow_scoring import WARSAW, _utc, expected_periods

@dataclass(frozen=True)
class SnapshotSelection:
    snapshot_id: str | None
    cutoff_utc: datetime
    rows: tuple
    rejected: tuple


def select_snapshot(day, snapshots):
    """Latest complete JWCD snapshot genuinely available by D-1 09:50 Warsaw.

    Canonical snapshot: id, delivery_date (date), retrieved_at (aware datetime),
    retrieval_completed_at (aware datetime), rows of period_start_utc,
    publication_ts (both aware), jwcd_mw (finite number). Missing completion or
    publication evidence rejects the candidate rather than inventing history.
    """
    cutoff = _utc(datetime.combine(day-timedelta(days=1),time(9,50),WARSAW))
    snapshots = list(snapshots)
    ids = Counter(str(s.get('id','')) for s in snapshots)
    eligible, rejected = [], []
    periods = set(expected_periods(day))
    for s in snapshots:
        sid = str(s.get('id',''))
        try:
            if not sid or ids[sid] != 1:
                raise ValueError('MISSING_OR_DUPLICATE_SNAPSHOT_ID')
            if s.get('delivery_date') != day:
                raise ValueError('WRONG_DELIVERY_DATE')
            retrieved = _utc(s.get('retrieved_at'))
            completed = _utc(s.get('retrieval_completed_at'))
            if not retrieved <= completed <= cutoff:
                raise ValueError('RETRIEVAL_AFTER_CUTOFF_OR_INVALID_ORDER')
            clean = {}
            for row in s.get('rows',[]):
                p = _utc(row.get('period_start_utc'))
                pub = _utc(row.get('publication_ts'))
                if pub > completed:
                    raise ValueError('PUBLICATION_AFTER_RETRIEVAL')
                if p in clean:
                    raise ValueError('DUPLICATE_UTC_PERIOD')
                raw = row.get('jwcd_mw')
                if isinstance(raw,bool):
                    raise ValueError('INVALID_JWCD')
                try:
                    value = Decimal(str(raw))
                except InvalidOperation as exc:
                    raise ValueError('INVALID_JWCD') from exc
                if not value.is_finite():
                    raise ValueError('INVALID_JWCD')
                clean[p] = value
            if set(clean) != periods:
                raise ValueError('INCOMPLETE_OR_WRONG_DELIVERY_PERIODS')
            eligible.append((completed,retrieved,sid,tuple(sorted(clean.items()))))
        except (ValueError,TypeError) as exc:
            rejected.append((sid,str(exc)))
    if not eligible:
        return SnapshotSelection(None,cutoff,(),tuple(rejected))
    _,_,sid,rows = max(eligible)
    return SnapshotSelection(sid,cutoff,rows,tuple(rejected))
