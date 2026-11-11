from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from shadow_history_audit import audit_rows, read_csv
from shadow_history_bundle import CSV_NAME, audit_bundle, compare_fix1_reference
from shadow_scoring import expected_periods


class HistoryBundleTests(unittest.TestCase):
    def test_official_reference_iterator_preserves_hourly_comparison(self):
        fixture = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-09/tge_delivery_2026-11-09.csv'
        history = list(read_csv(fixture))
        audit = audit_rows(history)
        matched = compare_fix1_reference(iter(history), audit, read_csv(fixture))
        self.assertEqual((matched['matched_days'], matched['compared_periods']), (1, 24))
        self.assertEqual(matched['different_periods'], 0)

        history[-1]['price_pln_mwh'] = '0'
        mismatch = compare_fix1_reference(iter(history), audit_rows(history), read_csv(fixture))
        self.assertEqual(mismatch['matched_days'], 0)
        self.assertEqual(mismatch['compared_periods'], 24)
        self.assertEqual(mismatch['compared_days'][0]['different_periods_utc'],
                         ['2026-11-09T22:00:00+00:00'])

    def make_bundle(self, path, *, manifest_changes=None, corrupt_csv=False,
                    days=("2026-10-24", "2026-10-26"), duplicate_manifest_field=None):
        workbook = b"opaque workbook bytes"
        csv_data = b"delivery_date,hour,fix1_pln_mwh,jwcd_mw\n" + b"".join(
            f"{day},H{hour:02d},-10,1200\n".encode()
            for day in days
            for hour in range(1, 25)
        )
        manifest = dict(
            hourly_rows=24 * len(days), days=len(days), first_date=days[0], last_date=days[-1],
            sha256={"test.xlsb": sha256(workbook).hexdigest(),
                    CSV_NAME: sha256(csv_data).hexdigest()},
        )
        manifest.update(manifest_changes or {})
        manifest_json = json.dumps(manifest)
        if duplicate_manifest_field is not None:
            key, value = duplicate_manifest_field
            token = json.dumps(key) + ":"
            manifest_json = manifest_json.replace(token, f"{token} {json.dumps(value)}, {token}", 1)
        with ZipFile(path, "w") as bundle:
            bundle.writestr("manifest.json", manifest_json)
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
        self.assertEqual(report["delivery_days_by_hours"], {24: 2})
        self.assertEqual(report["missing_delivery_days_by_hours"], {25: 1})
        self.assertEqual(report["missing_delivery_ranges"], [
            {"start_day": "2026-10-25", "end_day": "2026-10-25", "days": 1}
        ])
        self.assertEqual(report["days_by_issue"]["AVAILABILITY_NOT_PROVEN"], 2)
        self.assertEqual(report["model_ready_days"], 0)
        self.assertEqual(report["missing_timestamp_rows_by_field"], {
            "publication_ts": 48, "retrieved_at": 48,
            "retrieval_completed_at": 48, "actual_available_at": 48,
        })
        self.assertEqual(report["comparison_candidates"]["90D"], [])
        self.assertEqual(report["actual_availability_invalid_timestamp_rows"], 0)
        self.assertEqual(report["actual_availability_at_or_before_cutoff_rows"], 0)

    def test_reports_contiguous_gap_with_23_and_24_hour_days(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.zip"
            self.make_bundle(path, days=("2026-03-28", "2026-03-31"))
            report = audit_bundle(path)
        self.assertEqual(report["delivery_days_by_hours"], {24: 2})
        self.assertEqual(report["missing_delivery_days_by_hours"], {23: 1, 24: 1})
        self.assertEqual(report["missing_delivery_ranges"], [
            {"start_day": "2026-03-29", "end_day": "2026-03-30", "days": 2}
        ])

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

    def test_rejects_duplicate_coverage_and_checksum_fields_in_history_manifest(self):
        for key, value in (("days", 99), (CSV_NAME, "0" * 64), ("test.xlsb", "0" * 64)):
            with self.subTest(key=key), TemporaryDirectory() as tmp:
                path = Path(tmp) / "history.zip"
                self.make_bundle(path, duplicate_manifest_field=(key, value))
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "Duplicate history manifest field"):
                    audit_bundle(path)
                self.assertEqual(path.read_bytes(), before)

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
