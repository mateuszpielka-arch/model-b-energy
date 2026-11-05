"""Run the offline SHADOW comparison on audited, canonical hourly CSV history."""

import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json

from shadow_history_audit import FIX1_COLUMNS, _stamp, audit_rows, read_csv
from shadow_scoring import expected_periods
from shadow_walkforward import run_walkforward


def evaluate_csv_rows(rows, *, evaluated_at):
    """Report source readiness and compare only days admitted by the CSV audit.

    CSV provenance fields are assertions supplied by the source. The audit
    checks their presence and consistency but cannot authenticate them.
    """
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
    comparison = run_walkforward(
        history=history, target_days=[packet["day"] for packet in history],
        evaluated_at=evaluated_at,
    )
    return dict(history_audit=readiness, walkforward=comparison)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluated-at", required=True, type=_stamp)
    parser.add_argument("csv", nargs="+")
    args = parser.parse_args()
    rows = (row for path in args.csv for row in read_csv(path))
    print(json.dumps(evaluate_csv_rows(rows, evaluated_at=args.evaluated_at),
                     default=str, indent=2))


if __name__ == "__main__":
    main()
