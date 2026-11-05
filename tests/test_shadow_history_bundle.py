from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from shadow_history_bundle import CSV_NAME, audit_bundle


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
