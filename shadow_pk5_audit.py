"""Read-only audit of explicit local PK5 captures for one delivery day.

No Fix1 evidence, network, database, forecast or inferred capture timestamps.
A selected PK5 observation alone does not establish model-ready history.
"""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from zlib import error as ZlibError

from shadow_asof import select_snapshot
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_source_history import load_pk5_capture


def audit_pk5_captures(paths, *, day):
    """Validate every original capture before returning selection diagnostics."""
    paths = list(paths)
    if not paths:
        raise ValueError('At least one saved PK5 capture required')
    snapshots = []
    for path in paths:
        capture = load_pk5_capture(path, day=day)
        snapshots.append(prepare_pk5_snapshot(day=day.isoformat(), **capture))
    selected = select_snapshot(day, snapshots)
    chosen = next((item for item in snapshots if item['id'] == selected.snapshot_id), None)
    return dict(
        read_only=True, delivery_date=day.isoformat(),
        cutoff_utc=selected.cutoff_utc.isoformat(),
        selected_snapshot_id=selected.snapshot_id,
        captures=[dict(
            snapshot_id=item['id'], raw_sha256=item['raw_sha256'],
            source_url=item['source_url'], periods=len(item['rows']),
            retrieved_at_utc=item['retrieved_at'].isoformat(),
            retrieval_completed_at_utc=item['retrieval_completed_at'].isoformat(),
            publication_counts_utc=dict(sorted(Counter(
                row['publication_ts'].isoformat() for row in item['rows']).items())),
        ) for item in sorted(snapshots, key=lambda item: item['id'])],
        rejected_captures=[dict(snapshot_id=identifier, reason=reason)
                           for identifier, reason in sorted(selected.rejected)],
        selected_rows=[dict(
            period_start_utc=row['period_start_utc'].isoformat(),
            jwcd_mw=str(row['jwcd_mw']), publication_ts_utc=row['publication_ts'].isoformat(),
        ) for row in sorted(chosen['rows'], key=lambda row: row['period_start_utc'])] if chosen else [],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True, type=date.fromisoformat)
    parser.add_argument('--require-selection', action='store_true',
                        help='Exit 3 after full diagnostics when no whole capture can be selected')
    parser.add_argument('capture', nargs='+', type=Path, help='Saved capture.json paths')
    args = parser.parse_args()
    try:
        report = audit_pk5_captures(args.capture, day=args.day)
        result = json.dumps(report, indent=2) + '\n'
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(result, end='')
    if args.require_selection and report['selected_snapshot_id'] is None:
        raise SystemExit(3)


if __name__ == '__main__':
    main()
