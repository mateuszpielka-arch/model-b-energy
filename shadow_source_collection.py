"""Explicit offline export of multiple evidence-checked PK5/Fix1 source days.

Repeat --day MANIFEST CAPTURE [CAPTURE ...] for each delivery date.
Reads only supplied local files; no database, network, schedules or forecasts.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import sys
from zlib import error as ZlibError

from shadow_source_history import load_history_day, prepare_history_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', action='append', nargs='+', required=True, type=Path,
                        metavar='PATH', help='Fix1 manifest followed by one or more PK5 capture.json paths')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv')
    args = parser.parse_args()
    try:
        if any(len(group) < 2 for group in args.day):
            raise ValueError('Each --day requires a Fix1 manifest and at least one PK5 capture')
        days = [load_history_day(group[0], group[1:]) for group in args.day]
        report = prepare_history_collection(days=days)
        # Build the complete output before exposing any part of the collection.
        if args.format == 'json':
            result = json.dumps(report, indent=2) + '\n'
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
