"""Audit a PK5/Fix1 history ZIP in place, without extracting or writing data."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from hashlib import file_digest, sha256
from io import StringIO
import json
from zipfile import ZipFile

from shadow_history_audit import (
    FIX1_COLUMNS, _stamp, audit_rows, missing_delivery_ranges, read_csv, read_csv_rows,
)
from shadow_scoring import expected_periods

CSV_NAME = "fix1_jwcd_standard_days.csv"


def _unique_manifest_object(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"Duplicate history manifest field: {key}")
        fields[key] = value
    return fields


def audit_bundle(path, *, reference_paths=()):
    with ZipFile(path) as archive:
        names = archive.namelist()
        workbooks = [name for name in names if name.endswith(".xlsb")]
        if len(workbooks) != 1 or sorted(names) != sorted(
            [*workbooks, "manifest.json", CSV_NAME]
        ):
            raise ValueError("Expected exactly one workbook, CSV and manifest")
        workbook, = workbooks
        manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_manifest_object)
        checksums = manifest["sha256"]
        with archive.open(workbook) as stream:
            workbook_digest = file_digest(stream, "sha256").hexdigest()
        csv_bytes = archive.read(CSV_NAME)
        for name, digest in ((workbook, workbook_digest), (CSV_NAME, sha256(csv_bytes).hexdigest())):
            if checksums.get(name) != digest:
                raise ValueError(f"SHA-256 mismatch or missing checksum for {name}")

    with StringIO(csv_bytes.decode("utf-8-sig"), newline="") as stream:
        rows = read_csv_rows(stream, source=CSV_NAME)
    audit = audit_rows(rows)
    dates = [entry["day"] for entry in audit["days"]]
    if not dates or audit["invalid_date_rows"] or (
        len(rows), len(dates), dates[0], dates[-1]
    ) != tuple(manifest.get(field) for field in ("hourly_rows", "days", "first_date", "last_date")):
        raise ValueError("CSV coverage differs from the manifest")

    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    calendar = {first + timedelta(days=offset) for offset in range((last - first).days + 1)}
    missing = calendar - {date.fromisoformat(day) for day in dates}
    report = {
        "first_date": dates[0],
        "last_date": dates[-1],
        "hourly_rows": len(rows),
        "days": len(dates),
        "complete_pair_days": audit["complete_pair_days"],
        "model_ready_days": audit["model_ready_days"],
        "missing_timestamp_rows_by_field": audit["missing_timestamp_rows_by_field"],
        "actual_availability_invalid_timestamp_rows": audit["actual_availability_invalid_timestamp_rows"],
        "actual_availability_at_or_before_cutoff_rows": audit["actual_availability_at_or_before_cutoff_rows"],
        "delivery_days_by_hours": dict(sorted(Counter(
            entry["expected_periods"] for entry in audit["days"]
        ).items())),
        "missing_delivery_days_by_hours": dict(sorted(Counter(
            len(expected_periods(day)) for day in missing
        ).items())),
        "missing_delivery_ranges": missing_delivery_ranges(audit["days"], first, last),
        "days_by_issue": dict(sorted(Counter(
            issue for entry in audit["days"] for issue in entry["issues"]
        ).items())),
        "comparison_candidates": audit["comparison_candidates"],
        "comparison_blocked_by_late_fix1": audit["comparison_blocked_by_late_fix1"],
    }

    if reference_paths:
        reference_rows = [row for path in reference_paths for row in read_csv(path)]
        report["fix1_reference"] = compare_fix1_reference(rows, audit, reference_rows)
    return report


def compare_fix1_reference(rows, audit, reference_rows, *, history_range=None):
    """Compare complete price days by UTC period without assigning provenance.

    When rows are filtered to the reference dates, history_range can supply
    the first and last observed Fix1 dates as ISO strings.
    """
    reference_rows = list(reference_rows)

    def price_days(summary):
        return {
            day["day"] for day in summary["days"]
            if day["complete_periods"]
            and day["valid_fix1_rows"] == day["expected_periods"]
            and "HOUR_UTC_MISMATCH" not in day["issues"]
        }

    def prices_by_day(records, days):
        result = defaultdict(dict)
        periods = {day: expected_periods(date.fromisoformat(day)) for day in days}
        for row in records:
            try:
                day = date.fromisoformat(row["delivery_date"]).isoformat()
            except (KeyError, TypeError, ValueError):
                continue
            if day in days:
                period = (
                    _stamp(row["period_start_utc"]) if row.get("period_start_utc")
                    else periods[day][int(str(row["hour"]).removeprefix("H")) - 1]
                )
                value = next(row[name] for name in FIX1_COLUMNS if row.get(name) not in (None, ""))
                result[day][period] = Decimal(str(value))
        return result

    reference_audit = audit_rows(reference_rows)
    reference_days = {day["day"] for day in reference_audit["days"]}
    if not reference_days or reference_audit["invalid_date_rows"] or reference_days != price_days(reference_audit):
        raise ValueError("Reference CSV must contain complete, unambiguous Fix1 price days")

    history = prices_by_day(rows, price_days(audit) & reference_days)
    reference = prices_by_day(reference_rows, reference_days)
    compared = []
    for day in sorted(history):
        different = [
            period.isoformat() for period, price in sorted(reference[day].items())
            if history[day][period] != price
        ]
        compared.append({"day": day, "periods": len(reference[day]),
                         "different_periods_utc": different})
    unmatched = reference_days - history.keys()
    if history_range is None and audit["days"]:
        history_range = (audit["days"][0]["day"], audit["days"][-1]["day"])
    within_range = {
        day for day in unmatched
        if history_range is not None and history_range[0] <= day <= history_range[1]
    }
    return {
        "matched_days": sum(not day["different_periods_utc"] for day in compared),
        "compared_periods": sum(day["periods"] for day in compared),
        "different_periods": sum(len(day["different_periods_utc"]) for day in compared),
        "missing_or_invalid_history_days": sorted(unmatched),
        "missing_or_invalid_within_history_range_days": sorted(within_range),
        "outside_history_range_days": sorted(unmatched - within_range),
        "compared_days": compared,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip")
    parser.add_argument("--reference", nargs="+", default=())
    args = parser.parse_args()
    print(json.dumps(audit_bundle(args.zip, reference_paths=args.reference), indent=2))
