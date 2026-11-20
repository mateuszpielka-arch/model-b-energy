"""Read-only price-consistency audit of explicit local Fix1 capture manifests.

Counts distinct evidence separately from delivery days. Does not select an
archive winner, write a database, authenticate HTTP times or admit JWCD history.
"""
import argparse
from collections import defaultdict
import json

from shadow_ingest_manifest import load_batches


def audit_manifests(paths):
    paths = list(paths)
    if not paths:
        raise ValueError('At least one Fix1 capture manifest required')
    # Validate every manifest before constructing any report.
    entries = [item for path in paths for item in load_batches(path)]
    captures = {}
    for batch, _ in entries:
        key = (batch.identity, batch.retrieved_at, batch.source_url)
        captures[key] = batch
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
        days.append(dict(
            delivery_date=day.isoformat(), periods=len(values), captures=len(batches),
            price_consensus='CONFLICT' if conflicts else 'CONSISTENT',
            conflicting_periods_utc=conflicts,
            first_observed_retrieved_at_utc=batches[0].retrieved_at.isoformat(),
            evidence=[dict(
                raw_sha256=batch.raw_sha256, reference_sha256=batch.reference_sha256,
                parser_version=batch.parser_version, source_url=batch.source_url,
                retrieved_at_utc=batch.retrieved_at.isoformat(),
            ) for batch in batches],
        ))
    return dict(
        read_only=True, validated_capture_entries=len(entries), distinct_captures=len(captures),
        duplicate_capture_entries=len(entries) - len(captures), delivery_days=len(days),
        unique_delivery_periods=sum(day['periods'] for day in days),
        days_with_price_conflicts=sum(day['price_consensus'] == 'CONFLICT' for day in days),
        days=days,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='+', help='Explicit saved Fix1 manifest paths')
    args = parser.parse_args()
    try:
        result = json.dumps(audit_manifests(args.manifest), indent=2) + '\n'
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(result, end='')


if __name__ == '__main__':
    main()
