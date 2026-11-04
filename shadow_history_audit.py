"""Read-only CSV audit; no DB connections or production imports.

Supports legacy H01..H24 ONLY on standard days; DST needs explicit UTC periods.
A structurally complete price day alone never means model-ready history.
"""
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from shadow_asof import cutoff_for_day
from shadow_scoring import _utc, expected_periods


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
    for d,records in sorted(groups.items()):
        periods=expected_periods(d); expected=set(periods); seen=[]; issues=set()
        good_prices=good_jwcd=available=verified=0
        actual_times = []
        actual_versions = set()
        retrieval_windows = set()
        snapshot_ids = set()
        missing_timing = invalid_timing = late_timing = 0
        cutoff = cutoff_for_day(d)
        for r in records:
            try:
                if r.get('period_start_utc'):
                    p=_stamp(r['period_start_utc'])
                elif len(periods)==24:
                    label=str(r.get('hour','')).removeprefix('H')
                    h=int(label)
                    if not 1<=h<=24: raise ValueError('hour')
                    p=periods[h-1]
                else:
                    raise ValueError('DST_REQUIRES_UTC_PERIODS')
                seen.append(p)
            except (ValueError,TypeError,IndexError): issues.add('INVALID_OR_AMBIGUOUS_PERIOD')
            if _number(r.get('fix1',r.get('price_pln_mwh'))): good_prices+=1
            if _number(r.get('jwcd_mw')): good_jwcd+=1
            if r.get('actual_verified') in (True,'true','True'): verified+=1
            actual_versions.add(str(r.get('actual_version') or '').strip())
            snapshot_ids.add(str(r.get("jwcd_snapshot_id") or "").strip())
            try:
                actual_time = _stamp(r.get('actual_available_at'))
                if actual_time > cutoff:
                    actual_times.append(actual_time)
            except (ValueError, TypeError):
                pass
            canonical_start = r.get('retrieved_at')
            legacy_start = r.get('retrieved_at_utc')
            stamps = (r.get('publication_ts'), canonical_start or legacy_start,
                      r.get('retrieval_completed_at'))
            if any(stamp in (None, '') for stamp in stamps):
                missing_timing += 1
                continue
            try:
                pub, start, end = map(_stamp, stamps)
                if canonical_start and legacy_start and _stamp(legacy_start) != start:
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
        complete=len(records)==len(periods) and len(seen)==len(set(seen)) and set(seen)==expected
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
        days.append(dict(day=d.isoformat(),expected_periods=len(periods),rows=len(records),
            unique_periods=len(set(seen)),complete_periods=complete,
            valid_fix1_rows=good_prices,valid_jwcd_rows=good_jwcd,
            actual_after_issuance_rows=len(actual_times),
            availability_proven_rows=available,verified_actual_rows=verified,
            availability_missing_timestamp_rows=missing_timing,
            availability_invalid_timestamp_rows=invalid_timing,
            availability_after_cutoff_rows=late_timing,
            model_ready=not issues,issues=sorted(issues)))
        if not issues:
            ready_at[d] = max(actual_times)
    longest_run = run = 0
    previous = None
    for entry in days:
        if not entry['model_ready']:
            continue
        day = date.fromisoformat(entry['day'])
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        longest_run = max(longest_run, run)
        previous = day
    unavailable = datetime.max.replace(tzinfo=timezone.utc)
    comparison_candidates = {f'{window}D': [] for window in (30, 60, 90)}
    for day in ready_at:
        cutoff = cutoff_for_day(day)
        for window in (30, 60, 90):
            if all(ready_at.get(day - timedelta(days=i), unavailable) <= cutoff
                   for i in range(1, window + 1)):
                comparison_candidates[f'{window}D'].append(day.isoformat())
    first_candidate = {
        window: dates[0] if dates else None
        for window, dates in comparison_candidates.items()
    }
    return dict(days=days, invalid_date_rows=invalid_dates,
                model_ready_days=sum(d['model_ready'] for d in days),
                longest_model_ready_run=longest_run,
                first_comparison_candidate=first_candidate,
                comparison_candidates=comparison_candidates,
                # Coverage only: the model runner checks training provenance again.
                missing_days_for_first_comparison={
                    f'{window}D': max(0, window + 1 - longest_run)
                    for window in (30, 60, 90)
                })


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(line for line in f if not line.startswith('#')))


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('csv',nargs='+');args=p.parse_args()
    rows=[r for name in args.csv for r in read_csv(name)]
    print(json.dumps(audit_rows(rows),indent=2))
