"""Explicit read-only audit of existing schemas; no migration or model execution.

No ambient DATABASE_URL. Never exports credentials or complete raw payloads.
"""
from datetime import date, datetime, timedelta, timezone
from collections import Counter, defaultdict
from shadow_asof import cutoff_for_day, select_snapshot
from shadow_history_audit import (
    _number, _stamp, audit_rows, comparison_coverage, missing_delivery_ranges, read_csv,
)
from shadow_history_bundle import compare_fix1_reference
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


def summarize_delivery_coverage(snapshots, actuals, candidate_selection):
    """Combine source diagnostics per observed delivery day."""
    snapshot_counts = Counter(snapshot['day'] for snapshot in snapshots)
    actual_by_day = {actual['day']: actual for actual in actuals}
    coverage = []
    for day in sorted(snapshot_counts.keys() | actual_by_day.keys()):
        actual = actual_by_day.get(day)
        selection = candidate_selection.get(day)
        if selection is not None:
            pk5_status = 'PROVISIONAL' if selection['provisional_snapshot_id'] else 'CANDIDATES_REJECTED'
        elif snapshot_counts[day]:
            pk5_status = 'NO_HOURLY_CANDIDATE'
        else:
            pk5_status = 'MISSING'
        if actual is None:
            fix1_status = 'MISSING'
        elif not actual['structurally_complete']:
            fix1_status = 'INCOMPLETE'
        elif not actual['complete_finite_prices']:
            fix1_status = 'INVALID_PRICES'
        else:
            fix1_status = 'COMPLETE_UNVERIFIED'
        coverage.append(dict(day=day, pk5_status=pk5_status, fix1_status=fix1_status))
    return coverage


def provisional_comparison_coverage(
    actuals, candidate_selection, *, excluded_days=(), included_days=None,
):
    """Estimate coverage from legacy metadata without admitting model inputs."""
    ready_at = {
        date.fromisoformat(actual['day']): _stamp(actual['latest_retrieved_at_utc'])
        for actual in actuals
        if (included_days is None or actual['day'] in included_days)
        and actual['day'] not in excluded_days
        and actual['single_recorded_source']
        and (actual['expected_periods'] == 24 or actual['period_basis'] == 'EXPLICIT_UTC')
        and actual['next_delivery_training_timing'] in ('TIMING_CANDIDATE', 'AFTER_NEXT_ISSUE_CUTOFF')
        and candidate_selection.get(actual['day'], {}).get('provisional_snapshot_id')
    }
    longest_run, candidates, late_fix1 = comparison_coverage(ready_at)
    return dict(paired_days=len(ready_at), longest_paired_run=longest_run,
                comparison_candidates=candidates, comparison_blocked_by_late_fix1=late_fix1)


def table_columns(conn, table):
    return {name for (name,) in conn.execute(
        'SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass '
        'AND attnum > 0 AND NOT attisdropped', (table,),
    )}


def audit_connection(conn, *, start_day=None, end_day=None, reference_rows=None):
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError('Start day must not be after end day')
    bounds = (start_day, end_day)
    reference_days = []
    if reference_rows is not None:
        reference_rows = list(reference_rows)
        try:
            reference_days = sorted({date.fromisoformat(row['delivery_date']) for row in reference_rows})
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Reference CSV contains an invalid delivery date') from exc
        if any((start_day and day < start_day) or (end_day and day > end_day) for day in reference_days):
            raise ValueError('Reference CSV contains delivery dates outside the audit range')
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
            columns = table_columns(conn, 'raw.snapshots')
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
            columns = table_columns(conn, 'actual.tge_fix1')
            first_seen = 'first_seen_at_utc' if 'first_seen_at_utc' in columns else 'NULL::timestamptz'
            retrieved = 'retrieved_at_utc' if 'retrieved_at_utc' in columns else 'NULL::timestamptz'
            source_url = 'source_url' if 'source_url' in columns else 'NULL::text'
            raw_hash = 'raw_hash' if 'raw_hash' in columns else 'NULL::text'
            period_column = 'period_start_utc' if 'period_start_utc' in columns else 'NULL::timestamptz'
            source_present = f"(btrim({source_url}) <> '' AND {raw_hash} ~ '^[0-9A-Fa-f]{{64}}$')"
            rows=conn.execute(f'''SELECT delivery_date,count(*),
                array_agg(DISTINCT hour) FILTER (WHERE hour IS NOT NULL),
                count(*) FILTER (WHERE price_pln_mwh IN (60,2026)),
                count(*) FILTER (WHERE price_pln_mwh IS NULL),
                count(*) FILTER (WHERE price_pln_mwh::text IN ('NaN','Infinity','-Infinity')),
                max({first_seen}), max({retrieved}),
                count(*) FILTER (WHERE {first_seen} IS NULL OR {retrieved} IS NULL),
                count(*) FILTER (WHERE {retrieved} < {first_seen}),
                count(*) FILTER (WHERE {retrieved} > {first_seen}),
                count(*) FILTER (WHERE {source_present} IS NOT TRUE),
                count(DISTINCT (btrim({source_url}), lower({raw_hash})))
                    FILTER (WHERE {source_present}),
                array_agg({period_column} ORDER BY {period_column})
                    FILTER (WHERE {period_column} IS NOT NULL),
                array_agg(hour ORDER BY {period_column})
                    FILTER (WHERE {period_column} IS NOT NULL)
                FROM actual.tge_fix1 WHERE delivery_date BETWEEN
                COALESCE(%s::date, '-infinity'::date) AND COALESCE(%s::date, 'infinity'::date)
                GROUP BY delivery_date ORDER BY delivery_date''', bounds).fetchall()
            for (day,n,hours,suspect,missing_prices,nonfinite_prices,latest_first_seen,
                 latest_retrieved,missing_timing,invalid_timing,refetched,missing_source,source_count,
                 explicit_periods,explicit_hours) in rows:
                periods = expected_periods(day)
                expected = len(periods)
                observed = set(hours or [])
                required = set(range(1, expected + 1))
                if explicit_periods:
                    structurally_complete = (
                        n == expected == len(explicit_periods)
                        and set(explicit_periods) == set(periods)
                        and (expected != 24 or all(
                            hour is None or hour == index
                            for index, hour in enumerate(explicit_hours, 1)
                        ))
                    )
                else:
                    structurally_complete = n == expected and observed == required
                complete_prices = structurally_complete and missing_prices == nonfinite_prices == 0
                next_cutoff = cutoff_for_day(day + timedelta(days=1))
                if not complete_prices or missing_timing or invalid_timing:
                    training_timing = 'UNPROVEN'
                elif latest_retrieved <= cutoff_for_day(day):
                    training_timing = 'AT_OR_BEFORE_OWN_ISSUE_CUTOFF'
                elif latest_retrieved > next_cutoff:
                    training_timing = 'AFTER_NEXT_ISSUE_CUTOFF'
                else:
                    training_timing = 'TIMING_CANDIDATE'
                actuals.append(dict(day=day.isoformat(),rows=n,distinct_legacy_hours=len(observed),
                    expected_periods=expected,structurally_complete=structurally_complete,
                    period_basis='EXPLICIT_UTC' if explicit_periods else 'LEGACY_HOUR',
                    missing_price_rows=missing_prices,
                    nonfinite_price_rows=nonfinite_prices,
                    complete_finite_prices=complete_prices,
                    next_delivery_issue_cutoff_utc=next_cutoff.isoformat(),
                    next_delivery_training_timing=training_timing,
                    latest_first_seen_at_utc=latest_first_seen.astimezone(timezone.utc).isoformat()
                    if latest_first_seen is not None else None,
                    latest_retrieved_at_utc=latest_retrieved.astimezone(timezone.utc).isoformat()
                    if latest_retrieved is not None else None,
                    rows_missing_price_timing=missing_timing,
                    rows_with_invalid_price_timing=invalid_timing,
                    rows_retrieved_after_first_seen=refetched,
                    rows_without_usable_source_metadata=missing_source,
                    distinct_recorded_sources=source_count,
                    single_recorded_source=structurally_complete and missing_source == 0 and source_count == 1,
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
        coverage = summarize_delivery_coverage(snapshots, actuals, candidate_selection)
        report = dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),transaction_read_only=True,
                    start_day=start_day.isoformat() if start_day else None,
                    end_day=end_day.isoformat() if end_day else None,
                    tables=present,snapshots=snapshots,actual_days=actuals,
                    archive_timing_candidate_days=sorted({
                        s['day'] for s in snapshots if s.get('archive_timing_candidate')
                    }),
                    archive_hourly_candidate_selection=candidate_selection,
                    delivery_coverage=coverage,
                    missing_delivery_ranges=missing_delivery_ranges(coverage, start_day, end_day),
                    archive_hourly_candidate_days=sorted({
                        s['day'] for s in snapshots if s.get('archive_hourly_candidate')
                    }),
                    note='60/2026 are diagnostics, not proof of invalid price. Legacy retrieval time is not completion evidence.')
        excluded_days = set()
        if reference_rows is not None:
            history = []
            if present['actual.tge_fix1']:
                rows = conn.execute(
                    f'SELECT delivery_date,hour,price_pln_mwh,{period_column} FROM actual.tge_fix1 '
                    'WHERE delivery_date = ANY(%s::date[])', (reference_days,),
                )
                history = [
                    dict(delivery_date=day.isoformat(), hour=hour, fix1=price, period_start_utc=period)
                    for day, hour, price, period in rows
                ]
            comparison = compare_fix1_reference(history, audit_rows(history), reference_rows)
            report['fix1_reference'] = comparison
            excluded_days.update(comparison['missing_or_invalid_history_days'])
            excluded_days.update(entry['day'] for entry in comparison['compared_days']
                                 if entry['different_periods_utc'])
            matched_days = {entry['day'] for entry in comparison['compared_days']
                            if not entry['different_periods_utc']}
            report['reference_matched_comparison_coverage'] = provisional_comparison_coverage(
                actuals, candidate_selection, included_days=matched_days,
            )
        report['provisional_comparison_coverage'] = provisional_comparison_coverage(
            actuals, candidate_selection, excluded_days=excluded_days,
        )
        return report



if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dsn-file',required=True,help='Private local file containing authorized read-only connection DSN')
    p.add_argument('--start-day', type=date.fromisoformat, help='First delivery day, inclusive')
    p.add_argument('--end-day', type=date.fromisoformat, help='Last delivery day, inclusive')
    p.add_argument('--reference', nargs='+', help='Independent Fix1 reference CSV files')
    args=p.parse_args()
    if args.start_day and args.end_day and args.start_day > args.end_day:
        p.error('--start-day must not be after --end-day')
    from pathlib import Path
    dsn=Path(args.dsn_file).read_text().strip()
    reference_rows = None
    if args.reference:
        reference_rows = [row for path in args.reference for row in read_csv(path)]
    with connect_readonly(dsn) as conn:
        result = audit_connection(conn, start_day=args.start_day, end_day=args.end_day,
                                  reference_rows=reference_rows)
    print(json.dumps(result,indent=2))
