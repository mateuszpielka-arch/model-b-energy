"""Read-only price-consistency audit of explicit local Fix1 capture manifests.

Counts distinct evidence separately from delivery days. Does not select an
archive winner, write a database, authenticate HTTP times or admit JWCD history.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import json

from shadow_ingest_manifest import load_batches
from shadow_history_audit import missing_delivery_ranges
from shadow_scoring import expected_periods


def _evidence(batch):
    return dict(
        raw_sha256=batch.raw_sha256, reference_sha256=batch.reference_sha256,
        parser_version=batch.parser_version, source_url=batch.source_url,
        retrieved_at_utc=batch.retrieved_at.isoformat(),
    )


def audit_manifests(paths, *, start_day=None, end_day=None):
    for bound in (start_day, end_day):
        if bound is not None and (type(bound) is not date or bound in (date.min, date.max)):
            raise ValueError('Supported calendar date bounds required')
    if start_day is not None and end_day is not None and start_day > end_day:
        raise ValueError('Start day must not be after end day')
    paths = list(paths)
    if not paths:
        raise ValueError('At least one Fix1 capture manifest required')
    # Validate all evidence before reporting, but retain only parsed batches.
    # Keeping every raw HTML payload would grow with the entire corpus.
    capture_entries = 0
    captures = {}
    for path in paths:
        for batch, raw in load_batches(path):
            capture_entries += 1
            key = (batch.identity, batch.retrieved_at, batch.source_url)
            captures[key] = batch
            del raw
    grouped = defaultdict(list)
    for batch in captures.values():
        grouped[batch.delivery_date].append(batch)
    days = []
    for day, batches in sorted(grouped.items()):
        batches.sort(key=lambda batch: (batch.retrieved_at, batch.identity, batch.source_url))
        values = defaultdict(set)
        for batch in batches:
            for period, price in batch.rows:
                values[period].add(price)
        conflicts = [period.isoformat() for period, prices in sorted(values.items())
                     if len(prices) > 1]
        day_report = dict(
            delivery_date=day.isoformat(), periods=len(values), captures=len(batches),
            price_consensus='CONFLICT' if conflicts else 'CONSISTENT',
            conflicting_periods_utc=conflicts,
            first_observed_retrieved_at_utc=batches[0].retrieved_at.isoformat(),
            evidence=[_evidence(batch) for batch in batches],
        )
        if conflicts:
            price_maps = [(batch, dict(batch.rows)) for batch in batches]
            details = []
            for period, prices in sorted(values.items()):
                if len(prices) < 2:
                    continue
                by_price = defaultdict(list)
                for batch, price_map in price_maps:
                    by_price[price_map[period]].append(_evidence(batch))
                details.append(dict(
                    period_start_utc=period.isoformat(),
                    prices=[dict(price_pln_mwh=str(price), evidence=evidence)
                            for price, evidence in sorted(by_price.items())],
                ))
            day_report['price_conflicts'] = details
        days.append(day_report)
    report = dict(
        read_only=True, validated_capture_entries=capture_entries, distinct_captures=len(captures),
        duplicate_capture_entries=capture_entries - len(captures), delivery_days=len(days),
        unique_delivery_periods=sum(day['periods'] for day in days),
        days_with_price_conflicts=sum(day['price_consensus'] == 'CONFLICT' for day in days),
        days=days,
    )

    if start_day is not None or end_day is not None:
        first = start_day or min(grouped)
        last = end_day or max(grouped)
        if first > last:
            raise ValueError('Start day must not be after end day')
        calendar = Counter()
        observed = Counter()
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            hours = len(expected_periods(day))
            calendar[hours] += 1
            observed[hours] += day in grouped
        report['calendar_coverage'] = dict(
            scope='provided_manifests', start_day=first.isoformat(), end_day=last.isoformat(),
            calendar_days=sum(calendar.values()), observed_days=sum(observed.values()),
            missing_days=sum(calendar.values()) - sum(observed.values()),
            missing_ranges=missing_delivery_ranges(
                ({'day': day.isoformat()} for day in grouped), first, last,
            ),
            by_hours={str(hours): dict(
                calendar_days=calendar[hours], observed_days=observed[hours],
                missing_days=calendar[hours] - observed[hours],
                calendar_periods=hours * calendar[hours],
                missing_periods=hours * (calendar[hours] - observed[hours]),
            ) for hours in (23, 24, 25)},
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='+', help='Explicit saved Fix1 manifest paths')
    parser.add_argument('--start-day', type=date.fromisoformat, help='Calendar coverage start, inclusive')
    parser.add_argument('--end-day', type=date.fromisoformat, help='Calendar coverage end, inclusive')
    args = parser.parse_args()
    try:
        report = audit_manifests(args.manifest, start_day=args.start_day, end_day=args.end_day)
        result = json.dumps(report, indent=2) + '\n'
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(result, end='')


if __name__ == '__main__':
    main()
