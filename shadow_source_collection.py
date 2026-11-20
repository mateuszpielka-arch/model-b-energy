"""Explicit offline export of multiple evidence-checked PK5/Fix1 source days.

Repeat --day MANIFEST CAPTURE [CAPTURE ...] for each delivery date.
Reads only supplied local files; no database, network, schedules or issued forecasts.
"""
import argparse
import csv
from datetime import date
import io
import json
from pathlib import Path
import sys
from zlib import error as ZlibError

from shadow_source_history import load_history_day, prepare_history_collection
from shadow_history_audit import _stamp
from shadow_walkforward_csv import evaluate_csv_rows, _json_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', action='append', nargs='+', required=True, type=Path,
                        metavar='PATH', help='Fix1 manifest followed by one or more PK5 capture.json paths')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv')
    parser.add_argument('--walk-forward-at', type=_stamp,
                        help='Explicit evaluation timestamp; requires --format json and compares source days offline')
    args = parser.parse_args()
    if args.walk_forward_at is not None and args.format != 'json':
        parser.error('--walk-forward-at requires --format json')
    try:
        if any(len(group) < 2 for group in args.day):
            raise ValueError('Each --day requires a Fix1 manifest and at least one PK5 capture')
        days = [load_history_day(group[0], group[1:]) for group in args.day]
        report = prepare_history_collection(days=days)
        if args.walk_forward_at is not None:
            report['evaluation'] = dict(
                evaluated_at_utc=args.walk_forward_at.isoformat(),
                **evaluate_csv_rows(
                    report['rows'], evaluated_at=args.walk_forward_at,
                    target_from=date.fromisoformat(report['days'][0]['delivery_date']),
                    target_to=date.fromisoformat(report['days'][-1]['delivery_date']),
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
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    sys.stdout.write(result)


if __name__ == '__main__':
    main()
