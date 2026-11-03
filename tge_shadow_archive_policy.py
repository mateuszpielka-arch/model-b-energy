"""Pure, fail-closed plan for a separate VERIFIED Fix1 archive; no SQL or writes.

Legacy actual.tge_fix1 is not an authority. Never update or delete its rows.
Call only after raw-HTML parser validation and independent reference checks.
"""
from math import isfinite

HOURS = set(range(1, 25))


def plan_verified_promotion(candidate: dict[int, float], reference: dict[int, float],
                            existing_verified: dict[int, float]) -> dict:
    """Return a proposed immutable insert set, or quarantine all on any conflict.

    existing_verified is read-only data from a *separate* trusted verified table,
    never from the corrupt legacy actual.tge_fix1 table.
    """
    if set(candidate) != HOURS or set(reference) != HOURS:
        raise ValueError('Candidate and independent reference must have 24 unique hours')
    if not set(existing_verified).issubset(HOURS):
        raise ValueError('Unexpected hour in existing verified archive')
    if not all(isfinite(v) for m in (candidate, reference, existing_verified) for v in m.values()):
        raise ValueError('Non-finite price')
    mismatches = [h for h in range(1, 25) if abs(candidate[h] - reference[h]) > 0.005]
    if mismatches:
        return {'status': 'QUARANTINE_REFERENCE_MISMATCH', 'insert_rows': [],
                'conflict_hours': mismatches, 'already_verified_hours': []}
    conflicts = [h for h, v in existing_verified.items() if abs(v - candidate[h]) > 0.005]
    if conflicts:
        return {'status': 'QUARANTINE_IMMUTABLE_CONFLICT', 'insert_rows': [],
                'conflict_hours': sorted(conflicts), 'already_verified_hours': []}
    inserts = [(h, candidate[h]) for h in range(1, 25) if h not in existing_verified]
    return {'status': 'APPEND_ONLY' if inserts else 'ALREADY_VERIFIED',
            'insert_rows': inserts, 'conflict_hours': [],
            'already_verified_hours': sorted(existing_verified)}
