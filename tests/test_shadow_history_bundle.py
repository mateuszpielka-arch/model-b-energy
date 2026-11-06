from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from shadow_history_bundle import CSV_NAME, audit_bundle
from shadow_scoring import expected_periods


class HistoryBundleTests(unittest.TestCase):
    def make_bundle(self, path, *, manifest_changes=None, corrupt_csv=False):
        workbook = b"opaque workbook bytes"
        csv_data = b"delivery_date,hour,fix1_pln_mwh,jwcd_mw\n" + b"".join(
            f"{day},H{hour:02d},-10,1200\n".encode()
            for day in ("2026-10-24", "2026-10-26")
            for hour in range(1, 25)
        )
        manifest = dict(
            hourly_rows=48, days=2, first_date="2026-10-24", last_date="2026-10-26",
            sha256={"test.xlsb": sha256(workbook).hexdigest(),
                    CSV_NAME: sha256(csv_data).hexdigest()},
        )
        manifest.update(manifest_changes or {})
        with ZipFile(path, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            bundle.writestr("test.xlsb", workbook)
            bundle.writestr(CSV_NAME, csv_data + (b"changed" if corrupt_csv else b""))

    def test_read_only_audit_identifies_missing_autumn_day_and_unproven_availability(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.zip"
            self.make_bundle(path)
            before = path.read_bytes()
            report = audit_bundle(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
        self.assertEqual(report["complete_pair_days"], 2)
        self.assertEqual(report["missing_delivery_days_by_hours"], {25: 1})
        self.assertEqual(report["days_by_issue"]["AVAILABILITY_NOT_PROVEN"], 2)
        self.assertEqual(report["model_ready_days"], 0)
        self.assertEqual(report["missing_timestamp_rows_by_field"], {
            "publication_ts": 48, "retrieved_at": 48,
            "retrieval_completed_at": 48, "actual_available_at": 48,
        })
        self.assertEqual(report["comparison_candidates"]["90D"], [])

    def test_rejects_changed_csv_and_inconsistent_manifest(self):
        for options, error in (
            ({"corrupt_csv": True}, "SHA-256 mismatch"),
            ({"manifest_changes": {"days": 3}}, "coverage differs"),
        ):
            with self.subTest(options=options), TemporaryDirectory() as tmp:
                path = Path(tmp) / "history.zip"
                self.make_bundle(path, **options)
                with self.assertRaisesRegex(ValueError, error):
                    audit_bundle(path)

    def test_reference_reports_price_difference_and_absent_history_day(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.zip"
            self.make_bundle(path)
            reference = Path(tmp) / "reference.csv"
            reference.write_text(
                "delivery_date,hour,price_pln_mwh,period_start_utc\n" + "".join(
                    f"{day},H{hour:02d},{-9 if day == '2026-10-26' and hour == 24 else -10},\n"
                    for day in ("2026-10-24", "2026-10-26", "2026-10-27")
                    for hour in range(1, 25)
                ) + "".join(
                    f"2026-10-25,,-10,{period.isoformat()}\n"
                    for period in expected_periods(date(2026, 10, 25))
                ), encoding="utf-8",
            )
            report = audit_bundle(path, reference_paths=[reference])
            comparison = report["fix1_reference"]
            self.assertEqual(comparison["matched_days"], 1)
            self.assertEqual(comparison["compared_periods"], 48)
            self.assertEqual(comparison["different_periods"], 1)
            self.assertEqual(comparison["compared_days"][1]["different_periods_utc"],
                             ["2026-10-26T22:00:00+00:00"])
            self.assertEqual(comparison["missing_or_invalid_history_days"], ["2026-10-25", "2026-10-27"])
            self.assertEqual(comparison["missing_or_invalid_within_history_range_days"], ["2026-10-25"])
            self.assertEqual(comparison["outside_history_range_days"], ["2026-10-27"])
            self.assertEqual(report["model_ready_days"], 0)

            with reference.open("a", encoding="utf-8") as stream:
                stream.write("2026-10-24,H01,-10,\n")
            with self.assertRaisesRegex(ValueError, "complete, unambiguous"):
                audit_bundle(path, reference_paths=[reference])
