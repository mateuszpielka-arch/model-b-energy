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


def audit_pk5_utc_hours(day, rows, *, late_publication_rows=(), invalid_publication_rows=()):
    """Check explicit canonical period starts; legacy labels cannot disambiguate DST."""
    expected = set(expected_periods(day))
    periods = Counter()
    valid_jwcd_periods = set()
    periods_with_late_publication = set()
    periods_with_invalid_publication = set()
    missing_utc = invalid_utc = valid_jwcd = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid_utc += 1
            continue
        has_valid_jwcd = _number(row.get('fcst_gen_unit_stor_prov'))
        valid_jwcd += has_valid_jwcd
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
        if index in late_publication_rows:
            periods_with_late_publication.add(period)
        if index in invalid_publication_rows:
            periods_with_invalid_publication.add(period)
        if has_valid_jwcd:
            valid_jwcd_periods.add(period)
    gaps = _utc_period_gaps(expected, periods)
    return {
        'complete_utc_hours': not (any(gaps.values()) or missing_utc or invalid_utc),
        'rows_without_utc_period': missing_utc,
        'rows_with_invalid_utc_period': invalid_utc,
        **gaps,
        'valid_jwcd_rows': valid_jwcd,
        'observed_periods_with_publication_after_cutoff_utc': [
            period.isoformat() for period in sorted(expected & periods_with_late_publication)
        ],
        'observed_periods_with_missing_or_invalid_publication_utc': [
            period.isoformat() for period in sorted(expected & periods_with_invalid_publication)
        ],
        'observed_periods_without_valid_jwcd_utc': [
            period.isoformat() for period in sorted(expected & (periods.keys() - valid_jwcd_periods))
        ],
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


def _python_utc_timestamptz(columns, column):
    """Filter SQL timestamps that Python cannot decode in UTC."""
    if columns.get(column):
        return (
            f"CASE WHEN {column} >= TIMESTAMPTZ '0001-01-01 00:00:00+00' "
            f"AND {column} < TIMESTAMPTZ '10000-01-01 00:00:00+00' THEN {column} END"
        )
    return 'NULL::timestamptz'



# The audit needs both D-1 issuance and the next local midnight.
_MIN_DELIVERY_DAY = date.min + timedelta(days=1)
_MAX_DELIVERY_DAY = date.max - timedelta(days=1)


def _audit_bounds(start_day, end_day):
    first = max(start_day or _MIN_DELIVERY_DAY, _MIN_DELIVERY_DAY)
    last = min(end_day or _MAX_DELIVERY_DAY, _MAX_DELIVERY_DAY)
    return first, last


def _count_invalid_delivery_dates(conn, table, column):
    # Table and column names are fixed by the audit, never by CLI input.
    return conn.execute(
        f'SELECT count(*) FROM {table} WHERE {column} IS NULL OR {column} NOT BETWEEN %s AND %s',
        (_MIN_DELIVERY_DAY, _MAX_DELIVERY_DAY),
    ).fetchone()[0]


def audit_legacy_hourly(conn, start_day, end_day, *, reference_rows=None, reference_days=()):
    """Inventory legacy history without treating hour labels as timing evidence."""
    required = {'delivery_date', 'hour', 'jwcd_mw', 'fix1_pln_mwh'}
    missing = sorted(required - table_columns(conn, 'history.hourly').keys())
    days = []
    if not missing:
        bounds = _audit_bounds(start_day, end_day)
        conflicting_labels = defaultdict(list)
        for day, label in conn.execute("""
            SELECT delivery_date, hour
            FROM history.hourly
            WHERE delivery_date BETWEEN %s AND %s AND hour BETWEEN 1 AND 25
            GROUP BY delivery_date, hour
            HAVING count(DISTINCT jwcd_mw) FILTER (
                       WHERE jwcd_mw::text NOT IN ('NaN', 'Infinity', '-Infinity')
                   ) > 1
                OR count(DISTINCT fix1_pln_mwh) FILTER (
                       WHERE fix1_pln_mwh::text NOT IN ('NaN', 'Infinity', '-Infinity')
                   ) > 1
            ORDER BY delivery_date, hour
        """, bounds):
            conflicting_labels[day].append(label)
        rows = conn.execute("""
            SELECT delivery_date, count(*),
                   count(*) FILTER (WHERE valid_jwcd),
                   count(*) FILTER (WHERE valid_fix1),
                   array_agg(hour) FILTER (WHERE hour IS NOT NULL),
                   count(*) FILTER (WHERE hour IS NULL),
                   array_agg(DISTINCT hour) FILTER (
                       WHERE hour IS NOT NULL AND valid_jwcd AND valid_fix1
                   )
            FROM (
                SELECT delivery_date, hour,
                       jwcd_mw::text NOT IN ('NaN', 'Infinity', '-Infinity') AS valid_jwcd,
                       fix1_pln_mwh::text NOT IN ('NaN', 'Infinity', '-Infinity') AS valid_fix1
                FROM history.hourly
                WHERE delivery_date BETWEEN %s AND %s
            ) AS hourly
            GROUP BY delivery_date
            ORDER BY delivery_date
        """, bounds).fetchall()
        for day, count, jwcd, fix1, labels, unlabeled, paired_labels in rows:
            expected = len(expected_periods(day))
            nominal_labels = set(range(1, expected + 1))
            paired_hours = nominal_labels.intersection(paired_labels or ())
            label_counts = Counter(labels or ())
            days.append(dict(
                day=day.isoformat(), rows=count, expected_periods=expected,
                distinct_legacy_hours=len(nominal_labels & label_counts.keys()),
                valid_jwcd_rows=jwcd, valid_fix1_rows=fix1,
                paired_legacy_hours=len(paired_hours),
                duplicate_legacy_hour_labels=sorted(
                    label for label, occurrences in label_counts.items() if occurrences > 1
                ),
                conflicting_legacy_hour_labels=[
                    label for label in conflicting_labels.get(day, ()) if label in nominal_labels
                ],
                out_of_range_legacy_hour_labels=sorted(label_counts.keys() - nominal_labels),
                rows_without_legacy_hour_label=unlabeled,
                missing_pair_legacy_hours=sorted(nominal_labels - paired_hours) if expected == 24 else None,
                complete_pairs=expected == count == len(paired_hours) == 24,
                model_ready=False,
            ))
    report = dict(
        days=days, missing_columns=missing,
        missing_delivery_ranges=[] if missing else missing_delivery_ranges(days, start_day, end_day),
    )
    if reference_rows is not None and not missing:
        rows = conn.execute("""
            SELECT delivery_date, hour, fix1_pln_mwh
            FROM history.hourly
            WHERE delivery_date = ANY(%s::date[])
        """, (reference_days,)).fetchall()
        history = [
            dict(delivery_date=day.isoformat(), hour=hour, fix1=price)
            for day, hour, price in rows
        ]
        history_range = (days[0]['day'], days[-1]['day']) if days else None
        report['fix1_reference'] = compare_fix1_reference(
            history, audit_rows(history), reference_rows, history_range=history_range,
        )
    return report


def audit_connection(conn, *, start_day=None, end_day=None, reference_rows=None):
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError('Start day must not be after end day')
    bounds = _audit_bounds(start_day, end_day)
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
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        if conn.execute('SHOW transaction_read_only').fetchone()[0]!='on':
            raise ValueError('Database did not enforce READ ONLY')
        present={name:conn.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0] is not None
                 for name in ('raw.snapshots','actual.tge_fix1','history.hourly')}
        snapshots=[];actuals=[]
        hourly_candidates = defaultdict(list)
        if present['raw.snapshots']:
            columns = table_columns(conn, 'raw.snapshots')
            # These expressions are fixed identifiers or NULL for older schemas.
            retrieval_column = _python_utc_timestamptz(columns, 'retrieved_at_utc')
            archived_column = _python_utc_timestamptz(columns, 'created_at')
            source_column = 'source_version' if 'source_version' in columns else 'NULL'
            rows = conn.execute(
                f'SELECT id,business_date,{retrieval_column} AS retrieval_time,'
                f'payload,{archived_column},{source_column} '
                'FROM raw.snapshots WHERE business_date BETWEEN %s AND %s '
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
                    (index, row['publication_ts']) for index, row in enumerate(values)
                    if isinstance(row, dict) and row.get('publication_ts') not in (None, '')
                ]
                publications = []
                late_publication_rows = set()
                invalid_publication_rows = set(range(len(values)))
                for index, value in publication_values:
                    if not isinstance(value, str):
                        continue
                    try:
                        stamp = _stamp(value)
                    except (ValueError, OverflowError):
                        continue
                    publications.append(stamp)
                    invalid_publication_rows.remove(index)
                    if stamp > cutoff:
                        late_publication_rows.add(index)
                late_publications = len(late_publication_rows)
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
                archived_utc = (
                    archived.astimezone(timezone.utc)
                    if isinstance(archived, datetime) and archived.utcoffset() is not None else None
                )
                latest_publication = max(publications, default=None)
                publications_after_archive = (
                    sum(stamp > archived_utc for stamp in publications)
                    if archived_utc is not None else None
                )
                archive_timing_rejections = [
                    reason for rejected, reason in (
                        (source_version != 'energy-collector-v3', 'UNSUPPORTED_ARCHIVE_SOURCE'),
                        (retrieved_utc is None, 'MISSING_RETRIEVAL_START'),
                        (archived_utc is None, 'MISSING_ARCHIVE_TIMESTAMP'),
                        (retrieved_utc is not None and retrieved_utc > cutoff, 'RETRIEVAL_AFTER_CUTOFF'),
                        (archived_utc is not None and archived_utc > cutoff, 'ARCHIVE_AFTER_CUTOFF'),
                        (retrieved_utc is not None and archived_utc is not None
                         and archived_utc < retrieved_utc, 'ARCHIVE_BEFORE_RETRIEVAL'),
                        (not publications or len(publications) != len(values), 'INCOMPLETE_PUBLICATION_TIMESTAMPS'),
                        (bool(late_publications), 'PUBLICATION_AFTER_CUTOFF'),
                        (bool(publications_after_archive), 'PUBLICATION_AFTER_ARCHIVE'),
                    ) if rejected
                ]
                archive_timing_candidate = not archive_timing_rejections
                utc_hours = audit_pk5_utc_hours(
                    day, values, late_publication_rows=late_publication_rows,
                    invalid_publication_rows=invalid_publication_rows,
                )
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
                    publication_missing=len(values)-len(publication_values),
                    publication_unverified=len(publication_values)-len(publications),
                    publication_after_cutoff=late_publications,
                    publication_after_archive=publications_after_archive,
                    retrieved_at_utc=retrieved_utc.isoformat() if retrieved_utc is not None else None,
                    cutoff_utc=cutoff.isoformat(),
                    archived_at_utc=archived_utc.isoformat() if archived_utc is not None else None,
                    latest_publication_ts_utc=latest_publication.isoformat()
                    if latest_publication is not None else None,
                    source_version=source_version,
                    archive_timing_candidate=archive_timing_candidate,
                    archive_timing_rejections=archive_timing_rejections,
                    utc_hour_audit=utc_hours,
                    archive_hourly_candidate=hourly_candidate,
                    availability_status=availability,
                    model_ready=False))
        if present['actual.tge_fix1']:
            columns = table_columns(conn, 'actual.tge_fix1')
            # Only finite, timezone-aware values can establish a UTC cutoff.
            first_seen = _python_utc_timestamptz(columns, 'first_seen_at_utc')
            retrieved = _python_utc_timestamptz(columns, 'retrieved_at_utc')
            source_url = 'source_url' if 'source_url' in columns else 'NULL::text'
            raw_hash = 'raw_hash' if 'raw_hash' in columns else 'NULL::text'
            hour_column = 'hour' if 'hour' in columns else 'NULL::int'
            stored_period = 'period_start_utc' if 'period_start_utc' in columns else 'NULL::timestamptz'
            period_column = _python_utc_timestamptz(columns, 'period_start_utc')
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
                    FILTER (WHERE {period_column} IS NOT NULL),
                array_agg(DISTINCT {period_column}) FILTER (
                    WHERE {period_column} IS NOT NULL
                      AND {retrieved} > ((delivery_date + TIME '09:50') AT TIME ZONE 'Europe/Warsaw')
                ),
                array_agg(DISTINCT {period_column}) FILTER (
                    WHERE {period_column} IS NOT NULL
                      AND ({first_seen} IS NULL OR {retrieved} IS NULL OR {retrieved} < {first_seen})
                ),
                array_agg(DISTINCT {period_column}) FILTER (
                    WHERE {period_column} IS NOT NULL
                      AND price_pln_mwh::text NOT IN ('NaN','Infinity','-Infinity')
                )
                FROM actual.tge_fix1 WHERE delivery_date BETWEEN %s AND %s
                GROUP BY delivery_date ORDER BY delivery_date''', bounds).fetchall()
            for (day,n,hours,suspect,missing_prices,nonfinite_prices,latest_first_seen,
                 earliest_retrieved,latest_retrieved,missing_timing,invalid_timing,refetched,
                 missing_source,source_count,
                 invalid_periods,explicit_periods,explicit_hours,late_price_periods,
                 unproven_price_timing_periods,valid_price_periods) in rows:
                periods = expected_periods(day)
                expected = len(periods)
                observed = set(hours or [])
                required = set(range(1, expected + 1))
                utc_period_audit = None
                invalid_price_periods = None
                late_retrieval_periods = None
                unproven_timing_periods = None
                hour_utc_mismatches = []
                if explicit_periods or invalid_periods:
                    explicit_utc = [period.astimezone(timezone.utc) for period in explicit_periods or ()]
                    utc_period_audit = _utc_period_gaps(set(periods), Counter(explicit_utc))
                    if columns.get('retrieved_at_utc'):
                        late_retrieval_periods = [
                            period.isoformat() for period in sorted(set(periods) & set(late_price_periods or ()))
                        ]
                    unproven_timing_periods = [
                        period.isoformat()
                        for period in sorted(set(periods) & set(unproven_price_timing_periods or ()))
                    ]
                    invalid_price_periods = [
                        period.isoformat() for period in sorted(
                            (set(periods) & set(explicit_utc)) - set(valid_price_periods or ())
                        )
                    ]
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
                    observed_periods_without_valid_fix1_utc=invalid_price_periods,
                    hour_utc_mismatches=hour_utc_mismatches,
                    missing_price_rows=missing_prices,
                    nonfinite_price_rows=nonfinite_prices,
                    complete_finite_prices=complete_prices,
                    next_delivery_issue_cutoff_utc=next_cutoff.isoformat(),
                    next_delivery_training_timing=training_timing,
                    observed_periods_retrieved_after_next_issue_cutoff_utc=late_retrieval_periods,
                    latest_first_seen_at_utc=latest_first_seen.astimezone(timezone.utc).isoformat()
                    if latest_first_seen is not None else None,
                    earliest_retrieved_at_utc=earliest_retrieved.astimezone(timezone.utc).isoformat()
                    if earliest_retrieved is not None else None,
                    latest_retrieved_at_utc=latest_retrieved.astimezone(timezone.utc).isoformat()
                    if latest_retrieved is not None else None,
                    observed_periods_with_unproven_price_timing_utc=unproven_timing_periods,
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
        # Use the same calendar interval for both sources, even when only one is present.
        coverage_start = start_day
        coverage_end = end_day
        if coverage:
            coverage_start = coverage_start or date.fromisoformat(coverage[0]['day'])
            coverage_end = coverage_end or date.fromisoformat(coverage[-1]['day'])
        missing_ranges_by_source = {
            source: missing_delivery_ranges(
                (entry for entry in coverage if entry[status] != 'MISSING'),
                coverage_start, coverage_end,
            )
            for source, status in (('pk5', 'pk5_status'), ('fix1', 'fix1_status'))
        }
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
                    missing_delivery_ranges_by_source=missing_ranges_by_source,
                    archive_hourly_candidate_days=sorted({
                        s['day'] for s in snapshots if s.get('archive_hourly_candidate')
                    }),
                    note='60/2026 are diagnostics, not proof of invalid price. Legacy retrieval time is not completion evidence.')
        if present['history.hourly']:
            report['legacy_hourly_history'] = audit_legacy_hourly(
                conn, start_day, end_day, reference_rows=reference_rows, reference_days=reference_days,
            )
        report['invalid_delivery_date_rows_by_table'] = {
            table: _count_invalid_delivery_dates(conn, table, column)
            for table, column in (
                ('raw.snapshots', 'business_date'), ('actual.tge_fix1', 'delivery_date'),
                ('history.hourly', 'delivery_date'),
            )
            if present[table] and not (
                table == 'history.hourly'
                and column in report['legacy_hourly_history']['missing_columns']
            )
        }
        excluded_days = set()
        if reference_rows is not None:
            history = []
            invalid_period_days = {
                actual['day'] for actual in actuals if actual['rows_with_invalid_utc_period']
            }
            if present['actual.tge_fix1']:
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
                invalid_period_days=invalid_period_days,
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
