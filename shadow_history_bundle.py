"""Audit a PK5/Fix1 history ZIP in place, without extracting or writing data."""

import argparse
from collections import Counter
from datetime import date, timedelta
from hashlib import file_digest, sha256
from io import StringIO
import json
from zipfile import ZipFile

from shadow_history_audit import audit_rows, read_csv_rows
from shadow_scoring import expected_periods

CSV_NAME = "fix1_jwcd_standard_days.csv"


def audit_bundle(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        workbooks = [name for name in names if name.endswith(".xlsb")]
        if len(workbooks) != 1 or sorted(names) != sorted(
            [*workbooks, "manifest.json", CSV_NAME]
        ):
            raise ValueError("Expected exactly one workbook, CSV and manifest")
        workbook, = workbooks
        manifest = json.loads(archive.read("manifest.json"))
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
    return {
        "first_date": dates[0],
        "last_date": dates[-1],
        "hourly_rows": len(rows),
        "days": len(dates),
        "complete_pair_days": audit["complete_pair_days"],
        "model_ready_days": audit["model_ready_days"],
        "missing_delivery_days_by_hours": dict(sorted(Counter(
            len(expected_periods(day)) for day in missing
        ).items())),
        "days_by_issue": dict(sorted(Counter(
            issue for entry in audit["days"] for issue in entry["issues"]
        ).items())),
        "comparison_candidates": audit["comparison_candidates"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip")
    print(json.dumps(audit_bundle(parser.parse_args().zip), indent=2))
