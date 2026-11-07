"""Run the offline SHADOW comparison on audited, canonical hourly CSV history."""

import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json

from shadow_history_audit import FIX1_COLUMNS, _stamp, audit_rows, missing_delivery_ranges, read_csv
from shadow_scoring import expected_periods
from shadow_walkforward import run_walkforward


def evaluate_csv_rows(rows, *, evaluated_at, target_from=None, target_to=None):
    """Report source readiness and compare only days admitted by the CSV audit.

    CSV provenance fields are assertions supplied by the source. The audit
    checks their presence and consistency but cannot authenticate them.
    The inclusive target range limits evaluation only. Earlier admitted rows
    remain available for the per-target training windows.
    """
    if target_from is not None and target_to is not None and target_from > target_to:
        raise ValueError("Target start must not be after target end")
    rows = list(rows)
    readiness = audit_rows(rows)
    ready_days = {entry["day"] for entry in readiness["days"] if entry["model_ready"]}
    grouped = defaultdict(list)
    for row in rows:
        try:
            day = date.fromisoformat(row["delivery_date"])
        except (KeyError, TypeError, ValueError):
            continue
        if day.isoformat() in ready_days:
            grouped[day].append(row)

    history = []
    for day, records in sorted(grouped.items()):
        periods = expected_periods(day)
        actual, jwcd = [], []
        for row in records:
            utc_label = row.get("period_start_utc")
            period = (_stamp(utc_label) if utc_label else
                      periods[int(str(row["hour"]).removeprefix("H")) - 1])
            price = next(row[name] for name in FIX1_COLUMNS if row.get(name) not in (None, ""))
            actual.append((period, Decimal(str(price))))
            jwcd.append(dict(period_start_utc=period, publication_ts=_stamp(row["publication_ts"]),
                             jwcd_mw=row["jwcd_mw"]))
        first = records[0]
        history.append(dict(
            day=day, actual_verified=True, actual_version=str(first["actual_version"]).strip(),
            actual_available_at=max(_stamp(row["actual_available_at"]) for row in records),
            actual=actual,
            snapshots=[dict(
                id=str(first["jwcd_snapshot_id"]).strip(), delivery_date=day,
                retrieved_at=_stamp(first.get("retrieved_at") or first["retrieved_at_utc"]),
                retrieval_completed_at=_stamp(first["retrieval_completed_at"]), rows=jwcd,
            )],
        ))
    targets = [
        day for day in sorted(grouped)
        if (target_from is None or day >= target_from) and (target_to is None or day <= target_to)
    ]
    comparison = run_walkforward(history=history, target_days=targets, evaluated_at=evaluated_at)
    comparison["target_from"] = target_from.isoformat() if target_from is not None else None
    comparison["target_to"] = target_to.isoformat() if target_to is not None else None
    comparison["target_missing_ranges"] = missing_delivery_ranges(
        readiness["days"], target_from, target_to,
    )
    admission = {
        "delivery_days": len(readiness["days"]),
        "admitted_days": len(ready_days),
        "excluded_days": {
            entry["day"]: entry["issues"] for entry in readiness["days"]
            if not entry["model_ready"]
        },
    }
    return dict(history_audit=readiness, csv_admission=admission, walkforward=comparison)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluated-at", required=True, type=_stamp)
    parser.add_argument("--target-from", type=date.fromisoformat, help="First delivery date to score")
    parser.add_argument("--target-to", type=date.fromisoformat, help="Last delivery date to score")
    parser.add_argument("csv", nargs="+")
    args = parser.parse_args()
    if args.target_from is not None and args.target_to is not None and args.target_from > args.target_to:
        parser.error("--target-from must not be after --target-to")
    rows = (row for path in args.csv for row in read_csv(path))
    result = evaluate_csv_rows(
        rows, evaluated_at=args.evaluated_at, target_from=args.target_from, target_to=args.target_to,
    )
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
