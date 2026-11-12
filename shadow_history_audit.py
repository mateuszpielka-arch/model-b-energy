"""Read-only CSV audit; no DB connections or production imports.

Supports legacy H01..H24 ONLY on standard days; DST needs explicit UTC periods.
A structurally complete price day alone never means model-ready history.
"""
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import chain
from shadow_asof import cutoff_for_day, snapshot_identifier
from shadow_scoring import _utc, expected_periods


FIX1_COLUMNS = ('fix1', 'price_pln_mwh', 'fix1_pln_mwh')
JWCD_TIMESTAMPS = ('publication_ts', 'retrieved_at', 'retrieval_completed_at')


def _number(value):
    try: return value not in (None,'') and Decimal(str(value)).is_finite()
    except InvalidOperation: return False


def _stamp(value):
    if isinstance(value,datetime): return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace('Z','+00:00')))


def audit_rows(rows):
    groups=defaultdict(list); invalid_dates=0
    for row in rows:
        try: d=date.fromisoformat(row['delivery_date'])
        except (ValueError,KeyError,TypeError): invalid_dates+=1; continue
        groups[d].append(row)
    days=[]
    ready_at = {}
    missing_timestamp_totals = Counter(dict.fromkeys((*JWCD_TIMESTAMPS, 'actual_available_at'), 0))
    for d,records in sorted(groups.items()):
        periods=expected_periods(d); expected=set(periods); seen=[]; issues=set()
        good_prices=good_jwcd=available=verified=0
        actual_times = []
        invalid_actual_timestamps = premature_actual_timestamps = 0
        actual_versions = set()
        retrieval_windows = set()
        snapshot_ids = set()
        missing_timing = invalid_timing = late_timing = 0
        missing_timestamps = Counter(dict.fromkeys(missing_timestamp_totals, 0))
        cutoff = cutoff_for_day(d)
        conflicting_price_periods = set()
        uses_explicit_utc = any(r.get('period_start_utc') not in (None, '') for r in records)
        for r in records:
            p = None
            try:
                if r.get('period_start_utc') not in (None, ''):
                    p=_stamp(r['period_start_utc'])
                    if len(periods) == 24 and r.get("hour") not in (None, ""):
                        label = str(r["hour"]).removeprefix("H")
                        hour = int(label)
                        if not 1 <= hour <= 24 or p != periods[hour - 1]:
                            issues.add("HOUR_UTC_MISMATCH")
                elif len(periods) == 24 and not uses_explicit_utc:
                    label=str(r.get('hour','')).removeprefix('H')
                    h=int(label)
                    if not 1<=h<=24: raise ValueError('hour')
                    p=periods[h-1]
                else:
                    raise ValueError('Missing explicit UTC period')
                seen.append(p)
            except (ValueError,TypeError,IndexError): issues.add('INVALID_OR_AMBIGUOUS_PERIOD')
            price_fields = [r[name] for name in FIX1_COLUMNS
                            if r.get(name) not in (None, '')]
            if price_fields and all(_number(value) for value in price_fields):
                if len({Decimal(str(value)) for value in price_fields}) == 1:
                    good_prices += 1
                else:
                    issues.add('CONFLICTING_FIX1_COLUMNS')
                    if p is not None:
                        conflicting_price_periods.add(p)
            if _number(r.get('jwcd_mw')): good_jwcd+=1
            verification = r.get('actual_verified')
            if verification is True or (isinstance(verification, str) and verification in ('true', 'True')):
                verified += 1
            actual_version = r.get('actual_version')
            actual_versions.add(actual_version.strip() if isinstance(actual_version, str) else '')
            snapshot_ids.add(snapshot_identifier({"id": r.get("jwcd_snapshot_id")}))
            raw_actual_time = r.get('actual_available_at')
            if raw_actual_time in (None, ''):
                missing_timestamps['actual_available_at'] += 1
            else:
                try:
                    actual_time = _stamp(raw_actual_time)
                except (ValueError, TypeError):
                    invalid_actual_timestamps += 1
                else:
                    if actual_time <= cutoff:
                        premature_actual_timestamps += 1
                    else:
                        actual_times.append(actual_time)
            canonical_start = r.get('retrieved_at')
            legacy_start = r.get('retrieved_at_utc')
            has_canonical_start = canonical_start not in (None, '')
            start_stamp = canonical_start if has_canonical_start else legacy_start
            stamps = (r.get('publication_ts'), start_stamp, r.get('retrieval_completed_at'))
            missing_fields = [
                field for field, stamp in zip(JWCD_TIMESTAMPS, stamps) if stamp in (None, '')
            ]
            if missing_fields:
                missing_timestamps.update(missing_fields)
                missing_timing += 1
                continue
            try:
                pub, start, end = map(_stamp, stamps)
                if (has_canonical_start and legacy_start not in (None, '')
                        and _stamp(legacy_start) != start):
                    raise ValueError('Conflicting retrieval timestamps')
            except (ValueError, TypeError):
                invalid_timing += 1
                continue
            if start > end or pub > end:
                invalid_timing += 1
            elif end > cutoff:
                late_timing += 1
            else:
                available += 1
                retrieval_windows.add((start, end))
        period_counts = Counter(seen)
        missing = expected - period_counts.keys()
        unexpected = period_counts.keys() - expected
        duplicates = [p for p, count in period_counts.items() if count > 1]
        complete = len(records) == len(periods) and not (missing or unexpected or duplicates)
        if not complete: issues.add('INCOMPLETE_OR_DUPLICATE_PERIODS')
        if good_prices!=len(periods): issues.add('MISSING_OR_INVALID_FIX1')
        if good_jwcd!=len(periods): issues.add('MISSING_OR_INVALID_JWCD')
        if available!=len(periods): issues.add('AVAILABILITY_NOT_PROVEN')
        if len(retrieval_windows) > 1:
            issues.add("MIXED_JWCD_RETRIEVAL_WINDOWS")
        if verified!=len(periods): issues.add('ACTUAL_NOT_VERIFIED')
        if len(actual_times) != len(periods):
            issues.add('ACTUAL_AVAILABILITY_NOT_PROVEN')
        if '' in actual_versions:
            issues.add('MISSING_ACTUAL_VERSION')
        if len(actual_versions - {''}) > 1:
            issues.add('CONFLICTING_ACTUAL_VERSIONS')
        if "" in snapshot_ids:
            issues.add("MISSING_JWCD_SNAPSHOT_ID")
        if len(snapshot_ids - {""}) > 1:
            issues.add("MIXED_JWCD_SNAPSHOT_IDS")
        missing_timestamp_totals.update(missing_timestamps)
        days.append(dict(day=d.isoformat(),expected_periods=len(periods),rows=len(records),
            unique_periods=len(set(seen)),complete_periods=complete,
            missing_periods_utc=[p.isoformat() for p in sorted(missing)],
            unexpected_periods_utc=[p.isoformat() for p in sorted(unexpected)],
            duplicate_periods_utc=[p.isoformat() for p in sorted(duplicates)],
            conflicting_fix1_periods_utc=[p.isoformat() for p in sorted(conflicting_price_periods)],
            complete_pairs=(complete and "HOUR_UTC_MISMATCH" not in issues
                            and good_prices == good_jwcd == len(periods)),
            valid_fix1_rows=good_prices,valid_jwcd_rows=good_jwcd,
            actual_after_issuance_rows=len(actual_times),
            actual_availability_invalid_timestamp_rows=invalid_actual_timestamps,
            actual_availability_at_or_before_cutoff_rows=premature_actual_timestamps,
            availability_proven_rows=available,verified_actual_rows=verified,
            availability_missing_timestamp_rows=missing_timing,
            missing_timestamp_rows_by_field=dict(missing_timestamps),
            availability_invalid_timestamp_rows=invalid_timing,
            availability_after_cutoff_rows=late_timing,
            model_ready=not issues,issues=sorted(issues)))
        if not issues:
            ready_at[d] = max(actual_times)
    longest_run, comparison_candidates, late_fix1 = comparison_coverage(ready_at)
    first_candidate = {
        window: dates[0] if dates else None
        for window, dates in comparison_candidates.items()
    }
    return dict(days=days, invalid_date_rows=invalid_dates,
                missing_timestamp_rows_by_field=dict(missing_timestamp_totals),
                actual_availability_invalid_timestamp_rows=sum(
                    d['actual_availability_invalid_timestamp_rows'] for d in days),
                actual_availability_at_or_before_cutoff_rows=sum(
                    d['actual_availability_at_or_before_cutoff_rows'] for d in days),
                complete_pair_days=sum(d['complete_pairs'] for d in days),
                model_ready_days=sum(d['model_ready'] for d in days),
                longest_model_ready_run=longest_run,
                first_comparison_candidate=first_candidate,
                comparison_candidates=comparison_candidates,
                comparison_blocked_by_late_fix1=late_fix1,
                # Coverage only: the model runner checks training provenance again.
                missing_days_for_first_comparison={
                    f'{window}D': max(0, window + 1 - longest_run)
                    for window in (30, 60, 90)
                })


def missing_delivery_ranges(coverage, start_day, end_day):
    """Return compact inclusive ranges absent from observed delivery dates."""
    observed = sorted({date.fromisoformat(entry['day']).toordinal() for entry in coverage})
    if not observed and (start_day is None or end_day is None):
        return []
    first = start_day.toordinal() if start_day is not None else observed[0]
    last = end_day.toordinal() if end_day is not None else observed[-1]
    gaps = []
    cursor = first
    within_bounds = [day for day in observed if first <= day <= last]
    for boundary in (*within_bounds, last + 1):
        if cursor < boundary:
            gaps.append(dict(
                start_day=date.fromordinal(cursor).isoformat(),
                end_day=date.fromordinal(boundary - 1).isoformat(),
                days=boundary - cursor,
            ))
        cursor = boundary + 1
    return gaps


def comparison_coverage(ready_at):
    """Measure contiguous calendar coverage and respect each target cutoff."""
    longest_run = run = 0
    previous = None
    windows = (30, 60, 90)
    candidates = {f'{window}D': [] for window in windows}
    late_fix1 = {f'{window}D': {} for window in windows}
    for day in sorted(ready_at):
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        longest_run = max(longest_run, run)
        previous = day
        cutoff = cutoff_for_day(day)
        for window in windows:
            if run <= window:
                continue
            training_days = [day - timedelta(days=i) for i in range(window, 0, -1)]
            late_days = [prior.isoformat() for prior in training_days if ready_at[prior] > cutoff]
            key = f'{window}D'
            if late_days:
                late_fix1[key][day.isoformat()] = late_days
            else:
                candidates[key].append(day.isoformat())
    return longest_run, candidates, late_fix1


def read_csv_rows(lines, *, source):
    lines = iter(lines)
    header = None
    rows = []
    line_num = 0
    for line in lines:
        line_num += 1
        if line.startswith('#'):
            continue
        # Let the CSV reader consume continuation lines verbatim. Only a
        # comment at the start of a new record can be skipped.
        reader = csv.reader(chain((line,), lines), strict=True)
        record = next(reader)
        line_num += reader.line_num - 1
        if not record:
            continue
        if header is None:
            header = record
            duplicates = sorted(name for name, count in Counter(header).items() if count > 1)
            if duplicates:
                raise ValueError(f'{source}: duplicate CSV columns: {", ".join(duplicates)}')
            continue
        if len(record) != len(header):
            raise ValueError(f'{source}: CSV record at line {line_num} has wrong field count')
        rows.append(dict(zip(header, record, strict=True)))
    return rows


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return read_csv_rows(f, source=path)


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('csv',nargs='+');args=p.parse_args()
    rows=[r for name in args.csv for r in read_csv(name)]
    print(json.dumps(audit_rows(rows),indent=2))
