"""Run the offline SHADOW comparison on audited, canonical hourly CSV history."""

import argparse
from collections import defaultdict
from datetime import date
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path

from shadow_asof import snapshot_identifier
from shadow_history_audit import FIX1_COLUMNS, _stamp, audit_rows, missing_delivery_ranges, read_csv
from shadow_scoring import Score, expected_periods
from shadow_walkforward import run_walkforward


def evaluate_csv_rows(rows, *, evaluated_at, target_from=None, target_to=None, frozen_90d_by_day=None):
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
    excluded_days = {
        entry["day"]: entry["issues"] for entry in readiness["days"] if not entry["model_ready"]
    }
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
                id=snapshot_identifier({"id": first["jwcd_snapshot_id"]}), delivery_date=day,
                retrieved_at=_stamp(first.get("retrieved_at") or first["retrieved_at_utc"]),
                retrieval_completed_at=_stamp(first["retrieval_completed_at"]), rows=jwcd,
            )],
        ))
    def within_target_range(day):
        return (target_from is None or day >= target_from) and (target_to is None or day <= target_to)

    targets = [day for day in sorted(grouped) if within_target_range(day)]
    comparison = run_walkforward(
        history=history, target_days=targets, evaluated_at=evaluated_at,
        frozen_90d_by_day=frozen_90d_by_day,
    )
    observed_days = ready_days | excluded_days.keys()
    for skipped in comparison["skipped"]:
        missing = skipped.get("missing_training_days")
        if missing:
            skipped["training_days_excluded_by_csv_audit"] = {
                day: excluded_days[day] for day in missing if day in excluded_days
            }
            skipped["training_days_absent_from_csv"] = [day for day in missing if day not in observed_days]
    comparison["targets_excluded_by_csv_audit"] = {
        day: issues for day, issues in excluded_days.items()
        if within_target_range(date.fromisoformat(day))
    }
    comparison["target_from"] = target_from.isoformat() if target_from is not None else None
    comparison["target_to"] = target_to.isoformat() if target_to is not None else None
    comparison["target_missing_ranges"] = missing_delivery_ranges(
        readiness["days"], target_from, target_to,
    )
    admission = {
        "delivery_days": len(readiness["days"]),
        "admitted_days": len(ready_days),
        "excluded_days": excluded_days,
    }
    return dict(history_audit=readiness, csv_admission=admission, walkforward=comparison)


def _unique_frozen_object(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"Duplicate archived FROZEN field: {key}")
        record[key] = value
    return record


def read_frozen_90d(path):
    """Read explicit offline FROZEN records without inferring provenance."""
    with path.open(encoding="utf-8") as stream:
        records = json.load(stream, parse_float=Decimal, object_pairs_hook=_unique_frozen_object)
    if not isinstance(records, list):
        raise ValueError("Expected a JSON array of archived FROZEN forecasts")
    by_day = {}
    for index, record in enumerate(records, 1):
        try:
            day = date.fromisoformat(record["delivery_date"])
            price_pairs = record["prices"]
            if not isinstance(price_pairs, list) or any(
                not isinstance(pair, list) or len(pair) != 2 for pair in price_pairs
            ):
                raise ValueError("Expected price pairs as JSON arrays")
            prices = [(_stamp(period), value) for period, value in price_pairs]
            times = {field: _stamp(record[field])
                     for field in ("issued_at", "publication_ts", "retrieved_at")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid archived FROZEN forecast at entry {index}") from exc
        if day in by_day:
            raise ValueError(f"Duplicate archived FROZEN delivery date: {day}")
        by_day[day] = {**record, **times, "prices": prices}
    return by_day


def _json_value(value):
    if isinstance(value, Score):
        return asdict(value)
    return str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluated-at", required=True, type=_stamp)
    parser.add_argument("--target-from", type=date.fromisoformat, help="First delivery date to score")
    parser.add_argument("--target-to", type=date.fromisoformat, help="Last delivery date to score")
    parser.add_argument("--frozen-90d-json", type=Path, help="Local JSON export of archived FROZEN JWCD 90D forecasts")
    parser.add_argument("csv", nargs="+")
    args = parser.parse_args()
    if args.target_from is not None and args.target_to is not None and args.target_from > args.target_to:
        parser.error("--target-from must not be after --target-to")
    frozen = None
    if args.frozen_90d_json is not None:
        try:
            frozen = read_frozen_90d(args.frozen_90d_json)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    rows = (row for path in args.csv for row in read_csv(path))
    result = evaluate_csv_rows(
        rows, evaluated_at=args.evaluated_at, target_from=args.target_from, target_to=args.target_to,
        frozen_90d_by_day=frozen,
    )
    print(json.dumps(result, default=_json_value, indent=2))


if __name__ == "__main__":
    main()
