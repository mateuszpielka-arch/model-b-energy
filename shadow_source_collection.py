"""Explicit offline export of multiple evidence-checked PK5/Fix1 source days.

Repeat --day MANIFEST CAPTURE [CAPTURE ...] for each delivery date.
Reads only supplied local files; no database, network, schedules or issued forecasts.
"""
import argparse
import csv
from datetime import date
import io
import json
from os import fsync, link
from tempfile import NamedTemporaryFile
from pathlib import Path
import sys
from zlib import error as ZlibError

from shadow_source_history import load_history_collection
from shadow_history_audit import _stamp
from shadow_walkforward_csv import evaluate_csv_rows, _json_value


def write_output_exclusively(path, result):
    """Publish a complete UTF-8 export atomically, refusing any existing path."""
    staging = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                dir=path.parent, prefix='.fix1-export-', delete=False) as stream:
            staging = Path(stream.name)
            stream.write(result)
            stream.flush()
            fsync(stream.fileno())
        link(staging, path)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', action='append', nargs='+', required=True, type=Path,
                        metavar='PATH', help='Fix1 manifest followed by one or more PK5 capture.json paths')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv')
    parser.add_argument('--walk-forward-at', type=_stamp,
                        help='Explicit evaluation timestamp; requires --format json and compares source days offline')
    parser.add_argument('--target-from', type=date.fromisoformat,
                        help='First delivery date to evaluate; earlier source days remain training history')
    parser.add_argument('--target-to', type=date.fromisoformat,
                        help='Last delivery date to evaluate; defaults to last source date')
    parser.add_argument('--output', type=Path,
                        help='Publish a new local file atomically; refuses existing paths and suppresses stdout')
    args = parser.parse_args()
    if (args.target_from is not None or args.target_to is not None) and args.walk_forward_at is None:
        parser.error('Target range requires --walk-forward-at')
    if args.target_from is not None and args.target_to is not None and args.target_from > args.target_to:
        parser.error('--target-from must not be after --target-to')
    if args.walk_forward_at is not None and args.format != 'json':
        parser.error('--walk-forward-at requires --format json')
    try:
        if any(len(group) < 2 for group in args.day):
            raise ValueError('Each --day requires a Fix1 manifest and at least one PK5 capture')
        report = load_history_collection(args.day)
        if args.walk_forward_at is not None:
            report['evaluation'] = dict(
                evaluated_at_utc=args.walk_forward_at.isoformat(),
                **evaluate_csv_rows(
                    report['rows'], evaluated_at=args.walk_forward_at,
                    target_from=args.target_from or date.fromisoformat(report['days'][0]['delivery_date']),
                    target_to=args.target_to or date.fromisoformat(report['days'][-1]['delivery_date']),
                ),
            )
        # Build the complete output before exposing any part of the collection.
        if args.format == 'json':
            result = json.dumps(report, default=_json_value, indent=2) + '\n'
        else:
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=report['rows'][0], lineterminator='\n')
            writer.writeheader()
            writer.writerows(report['rows'])
            result = output.getvalue()
        if args.output is not None:
            write_output_exclusively(args.output, result)
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    if args.output is None:
        sys.stdout.write(result)


if __name__ == '__main__':
    main()
