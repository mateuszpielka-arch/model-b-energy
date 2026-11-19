"""Canonical standard-day PK5 candidate from saved public PSE JSON; no I/O."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_scoring import WARSAW, _utc, expected_periods


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate PK5 JSON field: {key}')
        result[key] = value
    return result


def _explicit_utc(value):
    if not isinstance(value, str):
        raise ValueError('Missing explicit PK5 UTC timestamp')
    parsed = datetime.fromisoformat(value)
    # PSE *_utc fields explicitly declare UTC even when their text has no suffix.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError('Non-UTC offset in explicit PK5 UTC field')
    return _utc(parsed)


def prepare_pk5_snapshot(*, raw, day, source_url, retrieved_at, retrieval_completed_at):
    """Check one complete 24h response and admit it at its own D-1 09:50.

    Source bytes and HTTP capture authenticity remain caller-owned evidence.
    plan_dtime_utc is checked as the end of the period named by the PSE label.
    DST source-label mapping is deliberately not admitted by this adapter.
    Only fcst_gen_unit_stor_prov supplies JWCD; other forecast variables are ignored.
    """
    delivery = date.fromisoformat(day)
    periods = expected_periods(delivery)
    if len(periods) != 24:
        raise ValueError('PK5 DST mapping requires separate verification')
    url = urlsplit(source_url)
    if (url.scheme != 'https' or url.netloc != 'api.raporty.pse.pl'
            or url.path != '/api/pk5l-wp' or url.fragment
            or parse_qs(url.query, keep_blank_values=True) != {
                '$filter': [f"business_date eq '{delivery.isoformat()}'"]
            }):
        raise ValueError('Unexpected PK5 source URL or delivery filter')
    if not isinstance(raw, bytes) or not raw or len(raw) > 8 * 1024 * 1024:
        raise ValueError('Nonempty PK5 JSON bytes up to 8 MiB required')
    started, completed = _utc(retrieved_at), _utc(retrieval_completed_at)
    cutoff = cutoff_for_day(delivery)
    if started > completed:
        raise ValueError('PK5 snapshot rejected: INVALID_RETRIEVAL_ORDER')
    if started > cutoff:
        raise ValueError('PK5 snapshot rejected: RETRIEVAL_STARTED_AFTER_CUTOFF')
    if completed > cutoff:
        raise ValueError('PK5 snapshot rejected: RETRIEVAL_COMPLETED_AFTER_CUTOFF')
    payload = json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique_object)
    if (not isinstance(payload, dict) or payload.get('nextLink') or payload.get('@odata.nextLink')
            or not isinstance(payload.get('value'), list) or len(payload['value']) != 24):
        raise ValueError('One complete PK5 response without pagination required')
    ends = {period + timedelta(hours=1): (index, period)
            for index, period in enumerate(periods)}
    rows = []
    for row in payload['value']:
        if not isinstance(row, dict) or row.get('business_date') != day:
            raise ValueError('PK5 row delivery date mismatch')
        end = _explicit_utc(row.get('plan_dtime_utc'))
        if end not in ends:
            raise ValueError('PK5 period end outside delivery')
        index, period = ends[end]
        if (row.get('period') != f'{index:02d} - {index + 1:02d}'
                or row.get('plan_dtime') != end.astimezone(WARSAW).strftime('%Y-%m-%d %H:%M:%S')):
            raise ValueError('PK5 local label/end disagrees with UTC')
        published = _explicit_utc(row.get('publication_ts_utc'))
        local_publication = row.get('publication_ts')
        if not isinstance(local_publication, str):
            raise ValueError('Missing PK5 local publication timestamp')
        if datetime.fromisoformat(local_publication) != published.astimezone(WARSAW).replace(tzinfo=None):
            raise ValueError('PK5 local publication disagrees with UTC')
        rows.append(dict(period_start_utc=period, publication_ts=published,
                         jwcd_mw=row.get('fcst_gen_unit_stor_prov')))
    raw_hash = sha256(raw).hexdigest()
    capture_identity = dict(
        raw_sha256=raw_hash, retrieved_at_utc=started.isoformat(),
        retrieval_completed_at_utc=completed.isoformat(),
    )
    capture_hash = sha256(json.dumps(
        capture_identity, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    snapshot = dict(
        id='pk5-capture-sha256:' + capture_hash, delivery_date=delivery,
        retrieved_at=started, retrieval_completed_at=completed,
        rows=tuple(rows), raw_sha256=raw_hash, source_url=source_url,
    )
    selection = select_snapshot(delivery, [snapshot])
    if selection.snapshot_id is None:
        raise ValueError('PK5 snapshot rejected: ' + ', '.join(reason for _, reason in selection.rejected))
    return snapshot
