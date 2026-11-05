"""Explicit read-only audit of existing schemas; no migration or model execution.

No ambient DATABASE_URL. Never exports credentials or complete raw payloads.
"""
from datetime import date, datetime, timezone
from collections import Counter, defaultdict
from shadow_asof import cutoff_for_day, select_snapshot
from shadow_history_audit import _number, _stamp
from shadow_scoring import expected_periods


def connect_readonly(dsn):
    import psycopg
    return psycopg.connect(dsn,autocommit=True,
        options='-c default_transaction_read_only=on -c statement_timeout=30000')


def audit_pk5_utc_hours(day, rows):
    """Check explicit canonical period starts; legacy labels cannot disambiguate DST."""
    expected = set(expected_periods(day))
    periods = Counter()
    missing_utc = invalid_utc = valid_jwcd = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_utc += 1
            continue
        valid_jwcd += _number(row.get('fcst_gen_unit_stor_prov'))
        raw_period = row.get('period_start_utc')
        if raw_period in (None, ''):
            missing_utc += 1
            continue
        try:
            period = _stamp(raw_period)
        except (ValueError, TypeError):
            invalid_utc += 1
            continue
        periods[period] += 1
    missing = expected - periods.keys()
    unexpected = periods.keys() - expected
    duplicates = {period for period, count in periods.items() if count > 1}
    return {
        'complete_utc_hours': not (missing or unexpected or duplicates or missing_utc or invalid_utc),
        'rows_without_utc_period': missing_utc,
        'rows_with_invalid_utc_period': invalid_utc,
        'missing_periods_utc': [period.isoformat() for period in sorted(missing)],
        'unexpected_periods_utc': [period.isoformat() for period in sorted(unexpected)],
        'duplicate_periods_utc': [period.isoformat() for period in sorted(duplicates)],
        'valid_jwcd_rows': valid_jwcd,
    }


def audit_connection(conn, *, start_day=None, end_day=None):
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError('Start day must not be after end day')
    bounds = (start_day, end_day)
    if not conn.autocommit: raise ValueError('Dedicated autocommit connection required')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if conn.execute('SHOW transaction_read_only').fetchone()[0]!='on':
            raise ValueError('Database did not enforce READ ONLY')
        present={name:conn.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0] is not None
                 for name in ('raw.snapshots','actual.tge_fix1','history.hourly')}
        snapshots=[];actuals=[]
        hourly_candidates = defaultdict(list)
        if present['raw.snapshots']:
            columns = {name for (name,) in conn.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'raw.snapshots'::regclass "
                "AND attnum > 0 AND NOT attisdropped"
            )}
            # Both expressions are fixed identifiers or NULL for older schemas.
            archived_column = 'created_at' if 'created_at' in columns else 'NULL'
            source_column = 'source_version' if 'source_version' in columns else 'NULL'
            rows = conn.execute(
                f'SELECT id,business_date,retrieved_at_utc,payload,{archived_column},{source_column} '
                'FROM raw.snapshots WHERE business_date BETWEEN '
                "COALESCE(%s::date, '-infinity'::date) AND COALESCE(%s::date, 'infinity'::date) "
                'ORDER BY business_date,retrieved_at_utc,id', bounds,
            ).fetchall()
            for sid,day,retrieved,payload,archived,source_version in rows:
                values=payload.get('pk5l-wp',[]) if isinstance(payload,dict) else None
                if not isinstance(values,list):
                    snapshots.append(dict(snapshot_id=sid,day=day.isoformat(),error='INVALID_PK5_ARRAY'));continue
                labels=[str(r.get('period','')) for r in values if isinstance(r,dict)]
                counts=Counter(labels)
                cutoff = cutoff_for_day(day)
                publication_values = [
                    row['publication_ts'] for row in values
                    if isinstance(row, dict) and row.get('publication_ts')
                ]
                publications = []
                for value in publication_values:
                    if not isinstance(value, str):
                        continue
                    try:
                        stamp = datetime.fromisoformat(value)
                    except ValueError:
                        continue
                    if stamp.utcoffset() is not None:
                        publications.append(stamp)
                late_publications = sum(stamp > cutoff for stamp in publications)
                if retrieved > cutoff:
                    availability = 'RETRIEVAL_STARTED_AFTER_CUTOFF'
                elif late_publications:
                    availability = 'PUBLICATION_AFTER_CUTOFF'
                else:
                    availability = 'UNPROVEN_NO_VERIFIED_COMPLETION_TIMESTAMP'
                # v3 fetches all pages before opening the insert transaction.
                # A recorded archive timestamp is a candidate bound, pending provenance review.
                archive_timing_candidate = (
                    source_version == 'energy-collector-v3'
                    and isinstance(archived, datetime) and archived.utcoffset() is not None
                    and retrieved <= archived <= cutoff
                    and bool(publications) and len(publications) == len(values)
                    and max(publications) <= archived
                )
                utc_hours = audit_pk5_utc_hours(day, values)
                hourly_candidate = (archive_timing_candidate and utc_hours['complete_utc_hours']
                                    and utc_hours['valid_jwcd_rows'] == len(values))
                if hourly_candidate:
                    hourly_candidates[day].append(dict(
                        id=sid, delivery_date=day, retrieved_at=retrieved,
                        retrieval_completed_at=archived,
                        rows=[dict(period_start_utc=_stamp(row['period_start_utc']),
                                   publication_ts=_stamp(row['publication_ts']),
                                   jwcd_mw=row['fcst_gen_unit_stor_prov']) for row in values],
                    ))
                snapshots.append(dict(snapshot_id=sid,day=day.isoformat(),expected_periods=len(expected_periods(day)),
                    rows=len(values),distinct_raw_labels=len(counts),repeated_raw_labels=sum(v-1 for v in counts.values()),
                    jwcd_nonnull=sum(isinstance(r,dict) and r.get('fcst_gen_unit_stor_prov') is not None for r in values),
                    publication_nonnull=len(publication_values),
                    publication_unverified=len(publication_values)-len(publications),
                    publication_after_cutoff=late_publications,
                    retrieved_at_utc=retrieved.isoformat(),
                    cutoff_utc=cutoff.isoformat(),
                    archived_at_utc=archived.astimezone(timezone.utc).isoformat()
                    if isinstance(archived, datetime) and archived.utcoffset() is not None else None,
                    source_version=source_version,
                    archive_timing_candidate=archive_timing_candidate,
                    utc_hour_audit=utc_hours,
                    archive_hourly_candidate=hourly_candidate,
                    availability_status=availability,
                    model_ready=False))
        if present['actual.tge_fix1']:
            rows=conn.execute('''SELECT delivery_date,count(*),
                array_agg(DISTINCT hour) FILTER (WHERE hour IS NOT NULL),
                count(*) FILTER (WHERE price_pln_mwh IN (60,2026))
                FROM actual.tge_fix1 WHERE delivery_date BETWEEN
                COALESCE(%s::date, '-infinity'::date) AND COALESCE(%s::date, 'infinity'::date)
                GROUP BY delivery_date ORDER BY delivery_date''', bounds).fetchall()
            for day,n,hours,suspect in rows:
                expected=len(expected_periods(day))
                observed = set(hours or [])
                required = set(range(1, expected + 1))
                actuals.append(dict(day=day.isoformat(),rows=n,distinct_legacy_hours=len(observed),
                    expected_periods=expected,structurally_complete=(n==expected and observed==required),
                    missing_legacy_hours=sorted(required-observed),
                    unexpected_legacy_hours=sorted(observed-required),
                    prices_equal_60_or_2026=suspect,
                    quality_status='UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION'))
        candidate_selection = {}
        for day, candidates in sorted(hourly_candidates.items()):
            selection = select_snapshot(day, candidates)
            candidate_selection[day.isoformat()] = {
                'provisional_snapshot_id': selection.snapshot_id,
                'rejected_snapshots': selection.rejected,
            }
        return dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),transaction_read_only=True,
                    start_day=start_day.isoformat() if start_day else None,
                    end_day=end_day.isoformat() if end_day else None,
                    tables=present,snapshots=snapshots,actual_days=actuals,
                    archive_timing_candidate_days=sorted({
                        s['day'] for s in snapshots if s.get('archive_timing_candidate')
                    }),
                    archive_hourly_candidate_selection=candidate_selection,
                    archive_hourly_candidate_days=sorted({
                        s['day'] for s in snapshots if s.get('archive_hourly_candidate')
                    }),
                    note='60/2026 are diagnostics, not proof of invalid price. Legacy retrieval time is not completion evidence.')


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dsn-file',required=True,help='Private local file containing authorized read-only connection DSN')
    p.add_argument('--start-day', type=date.fromisoformat, help='First delivery day, inclusive')
    p.add_argument('--end-day', type=date.fromisoformat, help='Last delivery day, inclusive')
    args=p.parse_args()
    if args.start_day and args.end_day and args.start_day > args.end_day:
        p.error('--start-day must not be after --end-day')
    from pathlib import Path
    dsn=Path(args.dsn_file).read_text().strip()
    with connect_readonly(dsn) as conn:
        result = audit_connection(conn, start_day=args.start_day, end_day=args.end_day)
    print(json.dumps(result,indent=2))
