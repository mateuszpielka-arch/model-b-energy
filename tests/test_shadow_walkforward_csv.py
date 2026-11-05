from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest

from shadow_history_audit import read_csv
from shadow_walkforward_csv import evaluate_csv_rows
import test_shadow_ols


class WalkforwardCSVTests(unittest.TestCase):
    def test_complete_csv_reaches_ranking_including_autumn_target(self):
        target = date(2026, 10, 25)
        args = test_shadow_ols.OLSTests().data(n=90, target=target)
        target_snapshot, = args["target_snapshots"]
        snapshots = [packet["snapshots"][0] for packet in args["history"]] + [target_snapshot]
        rows = []
        for snapshot in snapshots:
            day = snapshot["delivery_date"]
            for index, row in enumerate(snapshot["rows"], 1):
                rows.append(dict(
                    delivery_date=day.isoformat(), hour=f"H{index:02d}" if day != target else "",
                    period_start_utc=row["period_start_utc"].isoformat() if day == target else "",
                    fix1=str(2 * row["jwcd_mw"] + 10), jwcd_mw=str(row["jwcd_mw"]),
                    actual_verified="true", actual_version="test",
                    actual_available_at=(snapshot["retrieved_at"] + timedelta(hours=3)).isoformat(),
                    jwcd_snapshot_id=snapshot["id"], publication_ts=row["publication_ts"].isoformat(),
                    retrieved_at=snapshot["retrieved_at"].isoformat(),
                    retrieval_completed_at=snapshot["retrieval_completed_at"].isoformat(),
                ))
        rows[0]["delivery_date"] = rows[0]["delivery_date"].replace("-", "")
        evaluated_at = target_snapshot["retrieved_at"] + timedelta(days=2)
        result = evaluate_csv_rows(iter(rows), evaluated_at=evaluated_at)
        comparison = result["walkforward"]
        self.assertEqual(result["history_audit"]["model_ready_days"], 91)
        self.assertEqual(comparison["common_days"], 1)
        self.assertEqual(comparison["common_periods"], 25)
        self.assertEqual(len(comparison["ranking"]), 6)
        self.assertEqual(comparison["accepted"][0]["day"], target.isoformat())

        rows[0]["retrieval_completed_at"] = rows[0]["actual_available_at"]
        result = evaluate_csv_rows(rows, evaluated_at=evaluated_at)
        self.assertEqual(result["history_audit"]["model_ready_days"], 90)
        self.assertEqual(result["history_audit"]["days"][0]["availability_after_cutoff_rows"], 1)
        self.assertEqual(result["walkforward"]["ranking"], [])

    def test_real_reference_prices_do_not_create_ranking_without_jwcd(self):
        fixtures = Path(__file__).parent / "fixtures"
        rows = read_csv(fixtures / "tge_fix1_2026-11-03_reference.csv")
        result = evaluate_csv_rows(rows, evaluated_at=datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual(result["history_audit"]["days"][0]["valid_fix1_rows"], 24)
        self.assertEqual(result["history_audit"]["model_ready_days"], 0)
        self.assertEqual(result["walkforward"]["ranking"], [])
