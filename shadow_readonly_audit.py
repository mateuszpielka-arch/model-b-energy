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


def _utc_period_gaps(expected, observed):
    return {
        'missing_periods_utc': [period.isoformat() for period in sorted(expected - observed.keys())],
        'unexpected_periods_utc': [period.isoformat() for period in sorted(observed.keys() - expected)],
        'duplicate_periods_utc': sorted(period.isoformat() for period, count in observed.items() if count > 1),
    }


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
    gaps = _utc_period_gaps(expected, periods)
    return {
        'complete_utc_hours': not (any(gaps.values()) or missing_utc or invalid_utc),
        'rows_without_utc_period': missing_utc,
        'rows_with_invalid_utc_period': invalid_utc,
        **gaps,
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
    return dict(conn.execute(
        "SELECT attname, atttypid = 'timestamptz'::regtype "
        'FROM pg_attribute WHERE attrelid = %s::regclass '
        'AND attnum > 0 AND NOT attisdropped', (table,),
    ))


def _finite_timestamptz(columns, column):
    """Build an expression for a fixed timestamp column used by this audit."""
    if columns.get(column):
        return f'CASE WHEN isfinite({column}) THEN {column} END'
    return 'NULL::timestamptz'


def audit_legacy_hourly(conn, start_day, end_day):
    """Inventory legacy history without treating hour labels as timing evidence."""
    required = {'delivery_date', 'hour', 'jwcd_mw', 'fix1_pln_mwh'}
    missing = sorted(required - table_columns(conn, 'history.hourly').keys())
    days = []
    if not missing:
        rows = conn.execute("""
            SELECT delivery_date, count(*),
                   count(DISTINCT hour) FILTER (WHERE hour BETWEEN 1 AND 24),
                   count(*) FILTER (WHERE jwcd_mw::text NOT IN ('NaN', 'Infinity', '-Infinity')),
                   count(*) FILTER (WHERE fix1_pln_mwh::text NOT IN ('NaN', 'Infinity', '-Infinity'))
            FROM history.hourly
            WHERE delivery_date BETWEEN COALESCE(%s::date, '-infinity'::date)
                                    AND COALESCE(%s::date, 'infinity'::date)
            GROUP BY delivery_date
            ORDER BY delivery_date
        """, (start_day, end_day)).fetchall()
        for day, count, hours, jwcd, fix1 in rows:
            expected = len(expected_periods(day))
            days.append(dict(
                day=day.isoformat(), rows=count, expected_periods=expected,
                distinct_legacy_hours=hours, valid_jwcd_rows=jwcd, valid_fix1_rows=fix1,
                complete_pairs=expected == count == hours == jwcd == fix1 == 24,
                model_ready=False,
            ))
    return dict(
        days=days, missing_columns=missing,
        missing_delivery_ranges=[] if missing else missing_delivery_ranges(days, start_day, end_day),
    )


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
            # These expressions are fixed identifiers or NULL for older schemas.
            retrieval_column = _finite_timestamptz(columns, 'retrieved_at_utc')
            archived_column = _finite_timestamptz(columns, 'created_at')
            source_column = 'source_version' if 'source_version' in columns else 'NULL'
            rows = conn.execute(
                f'SELECT id,business_date,{retrieval_column} AS retrieval_time,'
                f'payload,{archived_column},{source_column} '
                'FROM raw.snapshots WHERE business_date BETWEEN '
                "COALESCE(%s::date, '-infinity'::date) AND COALESCE(%s::date, 'infinity'::date) "
                'ORDER BY business_date,retrieval_time,id', bounds,
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
                try:
                    retrieved_utc = _stamp(retrieved)
                except (ValueError, TypeError):
                    retrieved_utc = None
                if retrieved_utc is None:
                    availability = 'UNPROVEN_NO_VALID_RETRIEVAL_START'
                elif retrieved_utc > cutoff:
                    availability = 'RETRIEVAL_STARTED_AFTER_CUTOFF'
                elif late_publications:
                    availability = 'PUBLICATION_AFTER_CUTOFF'
                else:
                    availability = 'UNPROVEN_NO_VERIFIED_COMPLETION_TIMESTAMP'
                # v3 fetches all pages before opening the insert transaction.
                # A recorded archive timestamp is a candidate bound, pending provenance review.
                archive_timing_candidate = (
                    source_version == 'energy-collector-v3'
                    and retrieved_utc is not None
                    and isinstance(archived, datetime) and archived.utcoffset() is not None
                    and retrieved_utc <= archived <= cutoff
                    and bool(publications) and len(publications) == len(values)
                    and max(publications) <= archived
                )
                utc_hours = audit_pk5_utc_hours(day, values)
                hourly_candidate = (archive_timing_candidate and utc_hours['complete_utc_hours']
                                    and utc_hours['valid_jwcd_rows'] == len(values))
                if hourly_candidate:
                    hourly_candidates[day].append(dict(
                        id=sid, delivery_date=day, retrieved_at=retrieved_utc,
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
                    retrieved_at_utc=retrieved_utc.isoformat() if retrieved_utc is not None else None,
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
            # Only finite, timezone-aware values can establish a UTC cutoff.
            first_seen = _finite_timestamptz(columns, 'first_seen_at_utc')
            retrieved = _finite_timestamptz(columns, 'retrieved_at_utc')
            source_url = 'source_url' if 'source_url' in columns else 'NULL::text'
            raw_hash = 'raw_hash' if 'raw_hash' in columns else 'NULL::text'
            hour_column = 'hour' if 'hour' in columns else 'NULL::int'
            stored_period = 'period_start_utc' if 'period_start_utc' in columns else 'NULL::timestamptz'
            period_column = _finite_timestamptz(columns, 'period_start_utc')
            source_present = f"(btrim({source_url}) <> '' AND {raw_hash} ~ '^[0-9A-Fa-f]{{64}}$')"
            rows=conn.execute(f'''SELECT delivery_date,count(*),
                array_agg(DISTINCT {hour_column}) FILTER (WHERE {hour_column} IS NOT NULL),
                count(*) FILTER (WHERE price_pln_mwh IN (60,2026)),
                count(*) FILTER (WHERE price_pln_mwh IS NULL),
                count(*) FILTER (WHERE price_pln_mwh::text IN ('NaN','Infinity','-Infinity')),
                max({first_seen}), min({retrieved}), max({retrieved}),
                count(*) FILTER (WHERE {first_seen} IS NULL OR {retrieved} IS NULL),
                count(*) FILTER (WHERE {retrieved} < {first_seen}),
                count(*) FILTER (WHERE {retrieved} > {first_seen}),
                count(*) FILTER (WHERE {source_present} IS NOT TRUE),
                count(DISTINCT (btrim({source_url}), lower({raw_hash})))
                    FILTER (WHERE {source_present}),
                count({stored_period}) - count({period_column}),
                array_agg({period_column} ORDER BY {period_column}, {hour_column})
                    FILTER (WHERE {period_column} IS NOT NULL),
                array_agg({hour_column} ORDER BY {period_column}, {hour_column})
                    FILTER (WHERE {period_column} IS NOT NULL)
                FROM actual.tge_fix1 WHERE delivery_date BETWEEN
                COALESCE(%s::date, '-infinity'::date) AND COALESCE(%s::date, 'infinity'::date)
                GROUP BY delivery_date ORDER BY delivery_date''', bounds).fetchall()
            for (day,n,hours,suspect,missing_prices,nonfinite_prices,latest_first_seen,
                 earliest_retrieved,latest_retrieved,missing_timing,invalid_timing,refetched,
                 missing_source,source_count,
                 invalid_periods,explicit_periods,explicit_hours) in rows:
                periods = expected_periods(day)
                expected = len(periods)
                observed = set(hours or [])
                required = set(range(1, expected + 1))
                utc_period_audit = None
                hour_utc_mismatches = []
                if explicit_periods or invalid_periods:
                    explicit_utc = [period.astimezone(timezone.utc) for period in explicit_periods or ()]
                    utc_period_audit = _utc_period_gaps(set(periods), Counter(explicit_utc))
                    if expected == 24:
                        expected_hours = {period: hour for hour, period in enumerate(periods, 1)}
                        for period, hour in zip(explicit_utc, explicit_hours or (), strict=True):
                            expected_hour = expected_hours.get(period)
                            if hour is not None and expected_hour is not None and hour != expected_hour:
                                hour_utc_mismatches.append(dict(
                                    period_start_utc=period.isoformat(),
                                    expected_hour=expected_hour,
                                    observed_hour=hour,
                                ))
                    structurally_complete = (
                        not invalid_periods and n == expected and not any(utc_period_audit.values())
                        and not hour_utc_mismatches
                    )
                else:
                    # Local labels alone cannot identify the UTC periods on DST days.
                    structurally_complete = n == expected == 24 and observed == required
                complete_prices = structurally_complete and missing_prices == nonfinite_prices == 0
                own_cutoff = cutoff_for_day(day)
                next_cutoff = cutoff_for_day(day + timedelta(days=1))
                if not complete_prices or missing_timing or invalid_timing:
                    training_timing = 'UNPROVEN'
                elif latest_retrieved <= own_cutoff:
                    training_timing = 'AT_OR_BEFORE_OWN_ISSUE_CUTOFF'
                elif earliest_retrieved <= own_cutoff:
                    training_timing = 'MIXED_OWN_ISSUE_CUTOFF'
                elif latest_retrieved > next_cutoff:
                    training_timing = 'AFTER_NEXT_ISSUE_CUTOFF'
                else:
                    training_timing = 'TIMING_CANDIDATE'
                actuals.append(dict(day=day.isoformat(),rows=n,distinct_legacy_hours=len(observed),
                    expected_periods=expected,structurally_complete=structurally_complete,
                    period_basis='EXPLICIT_UTC' if explicit_periods or invalid_periods else 'LEGACY_HOUR',
                    rows_with_invalid_utc_period=invalid_periods,
                    utc_period_audit=utc_period_audit,
                    hour_utc_mismatches=hour_utc_mismatches,
                    missing_price_rows=missing_prices,
                    nonfinite_price_rows=nonfinite_prices,
                    complete_finite_prices=complete_prices,
                    next_delivery_issue_cutoff_utc=next_cutoff.isoformat(),
                    next_delivery_training_timing=training_timing,
                    latest_first_seen_at_utc=latest_first_seen.astimezone(timezone.utc).isoformat()
                    if latest_first_seen is not None else None,
                    earliest_retrieved_at_utc=earliest_retrieved.astimezone(timezone.utc).isoformat()
                    if earliest_retrieved is not None else None,
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
            timing = None
            if selection.snapshot_id is not None:
                timing = {
                    'cutoff_utc': selection.cutoff_utc.isoformat(),
                    'retrieved_at_utc': selection.retrieved_at_utc.isoformat(),
                    'latest_publication_ts_utc': selection.latest_publication_ts_utc.isoformat(),
                    # Selection uses archive time only as a provisional upper bound.
                    'archived_at_utc': selection.retrieval_completed_at_utc.isoformat(),
                }
            candidate_selection[day.isoformat()] = {
                'provisional_snapshot_id': selection.snapshot_id,
                'provisional_timing': timing,
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
        if present['history.hourly']:
            report['legacy_hourly_history'] = audit_legacy_hourly(conn, start_day, end_day)
        excluded_days = set()
        if reference_rows is not None:
            history = []
            if present['actual.tge_fix1']:
                invalid_period_days = {
                    actual['day'] for actual in actuals if actual['rows_with_invalid_utc_period']
                }
                comparable_days = [
                    day for day in reference_days if day.isoformat() not in invalid_period_days
                ]
                rows = conn.execute(
                    f'SELECT delivery_date,{hour_column},price_pln_mwh,{period_column} FROM actual.tge_fix1 '
                    'WHERE delivery_date = ANY(%s::date[])', (comparable_days,),
                )
                history = [
                    dict(delivery_date=day.isoformat(), hour=hour, fix1=price, period_start_utc=period)
                    for day, hour, price, period in rows
                ]
            # The reference query reads only matching dates. Use the full audited Fix1
            # range to classify missing dates within the same read-only snapshot.
            history_range = (actuals[0]['day'], actuals[-1]['day']) if actuals else None
            comparison = compare_fix1_reference(
                history, audit_rows(history), reference_rows, history_range=history_range,
            )
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
