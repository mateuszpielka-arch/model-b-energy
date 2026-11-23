"""Read-only multi-day PK5 evidence audit; no Fix1 or model-readiness inference."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from zlib import error as ZlibError

from shadow_pk5_audit import audit_pk5_captures
from shadow_scoring import expected_periods


def audit_pk5_history(groups):
    """Audit explicit day/capture groups, retaining each whole-capture decision.

    Gaps describe only the inclusive span between supplied delivery dates.
    No capture time, missing day, Fix1 value or forecast is fabricated.
    """
    if not isinstance(groups, (list, tuple)) or not groups:
        raise ValueError('Nonempty list or tuple of PK5 day groups required')
    seen = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'day', 'captures'}:
            raise ValueError('PK5 day group requires day and captures only')
        day = group['day']
        if type(day) is not date or day in (date.min, date.max):
            raise ValueError('Supported delivery date required')
        if day in seen:
            raise ValueError('Duplicate delivery date in PK5 history')
        seen.add(day)
        if not isinstance(group['captures'], (list, tuple)) or not group['captures']:
            raise ValueError('Nonempty list or tuple of PK5 capture paths required')
    # Validate all groups before returning any report to the caller.
    days = [audit_pk5_captures(group['captures'], day=group['day'])
            for group in sorted(groups, key=lambda group: group['day'])]
    labels = sorted(seen)
    gaps = [
        dict(start_day=(left + timedelta(days=1)).isoformat(),
             end_day=(right - timedelta(days=1)).isoformat(),
             days=(right - left).days - 1)
        for left, right in zip(labels, labels[1:])
        if (right - left).days > 1
    ]
    selected = [item for item in days if item['selected_snapshot_id'] is not None]
    return dict(
        read_only=True, model_readiness_assessed=False,
        first_delivery_date=labels[0].isoformat(), last_delivery_date=labels[-1].isoformat(),
        observed_days=len(days), selected_days=len(selected),
        unselected_days=len(days) - len(selected),
        observed_periods=sum(len(expected_periods(day)) for day in labels),
        selected_periods=sum(len(item['selected_rows']) for item in selected),
        missing_delivery_ranges=gaps, days=days,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-day', action='append', nargs='+', required=True,
                        metavar='DAY_OR_CAPTURE',
                        help='Delivery YYYY-MM-DD followed by capture.json paths; repeat per day')
    parser.add_argument('--require-selection', action='store_true',
                        help='Exit 3 after diagnostics if a supplied day has no selected capture')
    args = parser.parse_args()
    try:
        groups = []
        for values in args.source_day:
            if len(values) < 2:
                raise ValueError('Each --source-day requires a delivery date and capture paths')
            day = date.fromisoformat(values[0])
            if values[0] != day.isoformat():
                raise ValueError('Delivery date must use YYYY-MM-DD')
            groups.append(dict(day=day, captures=[Path(value) for value in values[1:]]))
        report = audit_pk5_history(groups)
        output = json.dumps(report, indent=2) + '\n'
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(output, end='')
    if args.require_selection and report['unselected_days']:
        raise SystemExit(3)


if __name__ == '__main__':
    main()
