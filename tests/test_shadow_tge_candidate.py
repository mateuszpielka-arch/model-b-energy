import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import unittest
from shadow_tge_candidate import validate_candidate


class CandidateTests(unittest.TestCase):
    def test_official_november_19_report_preserves_prices_and_utc(self):
        fixtures = Path(__file__).parent / "fixtures" / "tge_delivery_2026-11-19"
        entry, = json.loads((fixtures / "manifest.json").read_text())["entries"]
        raw = gzip.decompress((fixtures / entry["raw_file"]).read_bytes())
        reference = list(csv.DictReader(io.StringIO(
            (fixtures / entry["reference_file"]).read_text()
        )))
        result = validate_candidate("2026-11-19", raw)
        self.assertEqual(result["status"], "SHADOW_VALIDATED_LAYOUT_ONLY")
        self.assertEqual(result["row_count"], 24)
        self.assertEqual(result["sha256"], entry["raw_sha256"])
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["prices"], {
            row["hour"]: row["price_pln_mwh"] for row in reference
        })
        self.assertEqual(result["periods"], [
            {"tge_hour_label": row["hour"], "period_start_utc": row["period_start_utc"],
             "price_pln_mwh": row["price_pln_mwh"]}
            for row in reference
        ])
        self.assertFalse(result["archive_written"])
        self.assertFalse(result["scoring_written"])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_rejects_official_november_19_unpriced_capture(self):
        fixtures = Path(__file__).parent / "fixtures" / "tge_delivery_2026-11-19_unpriced"
        raw = gzip.decompress(
            (fixtures / "tge_delivery_2026-11-19_unpriced.html.gz").read_bytes()
        )
        with self.assertRaises(ValueError):
            validate_candidate("2026-11-19", raw)

    def test_dst_candidates_remain_blocked_before_layout_validation(self):
        for day in ("2026-03-29", "2026-10-25"):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, "DST day"):
                validate_candidate(day, b"<html></html>")

    def test_rejects_nonofficial_fragment_without_header(self):
        html = b"<table><tr><td>2026-11-17_H01</td><td>60</td><td>738,93</td></tr></table>"
        with self.assertRaises(ValueError):
            validate_candidate("2026-11-17", html)

    def test_rejects_missing_prices(self):
        html = b"<table id='rdn'><thead><tr><th></th></tr></thead><tbody></tbody></table>"
        with self.assertRaises(ValueError):
            validate_candidate("2026-11-17", html)


if __name__ == "__main__":
    unittest.main()
