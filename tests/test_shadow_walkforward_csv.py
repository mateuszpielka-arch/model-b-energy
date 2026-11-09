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
        self.assertEqual(result["csv_admission"], {
            "delivery_days": 91, "admitted_days": 91, "excluded_days": {},
        })
        self.assertEqual(comparison["common_days"], 1)
        self.assertEqual(comparison["common_periods"], 25)
        self.assertEqual(len(comparison["ranking"]), 6)
        self.assertEqual(comparison["accepted"][0]["day"], target.isoformat())

        scoped = evaluate_csv_rows(rows, evaluated_at=evaluated_at, target_from=target, target_to=target)
        self.assertEqual(scoped["csv_admission"], result["csv_admission"])
        self.assertEqual(scoped["walkforward"]["requested_days"], 1)
        self.assertEqual(scoped["walkforward"]["skipped"], [])
        self.assertEqual(scoped["walkforward"]["accepted"], comparison["accepted"])
        self.assertEqual(scoped["walkforward"]["ranking"], comparison["ranking"])
        self.assertEqual(scoped["walkforward"]["target_from"], target.isoformat())
        self.assertEqual(scoped["walkforward"]["target_to"], target.isoformat())

        rows[0]["retrieval_completed_at"] = rows[0]["actual_available_at"]
        result = evaluate_csv_rows(rows, evaluated_at=evaluated_at)
        self.assertEqual(result["history_audit"]["model_ready_days"], 90)
        self.assertEqual(result["history_audit"]["days"][0]["availability_after_cutoff_rows"], 1)
        self.assertEqual(result["walkforward"]["ranking"], [])
        first_day = snapshots[0]["delivery_date"].isoformat()
        self.assertEqual(result["csv_admission"], {
            "delivery_days": 91, "admitted_days": 90,
            "excluded_days": {first_day: ["AVAILABILITY_NOT_PROVEN"]},
        })
        self.assertNotIn(first_day, {entry["day"] for entry in result["walkforward"]["skipped"]})

    def test_real_reference_prices_do_not_create_ranking_without_jwcd(self):
        fixtures = Path(__file__).parent / "fixtures"
        rows = read_csv(fixtures / "tge_fix1_2026-11-03_reference.csv")
        result = evaluate_csv_rows(rows, evaluated_at=datetime(2026, 11, 4, tzinfo=timezone.utc))
        self.assertEqual(result["history_audit"]["days"][0]["valid_fix1_rows"], 24)
        self.assertEqual(result["history_audit"]["model_ready_days"], 0)
        self.assertEqual(result["walkforward"]["ranking"], [])

        self.assertEqual(result["csv_admission"]["delivery_days"], 1)
        self.assertEqual(result["csv_admission"]["admitted_days"], 0)
        self.assertIn("MISSING_OR_INVALID_JWCD",
                      result["csv_admission"]["excluded_days"]["2026-11-03"])
        self.assertEqual(result["walkforward"]["requested_days"], 0)

    def test_reversed_target_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Target start"):
            evaluate_csv_rows([], evaluated_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
                              target_from=date(2026, 11, 4), target_to=date(2026, 11, 3))

    def test_target_missing_ranges_distinguish_absent_from_rejected_days(self):
        fixtures = Path(__file__).parent / "fixtures"
        rows = read_csv(fixtures / "tge_delivery_2026-11-10/tge_delivery_2026-11-10.csv")
        rows += read_csv(fixtures / "tge_delivery_2026-11-09/tge_delivery_2026-11-09.csv")
        result = evaluate_csv_rows(
            rows, evaluated_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
            target_from=date(2026, 11, 10), target_to=date(2026, 11, 12),
        )
        excluded = result["csv_admission"]["excluded_days"]
        self.assertEqual(sorted(excluded), ["2026-11-09", "2026-11-10"])
        self.assertIn("MISSING_OR_INVALID_JWCD", excluded["2026-11-10"])
        self.assertEqual(result["walkforward"]["targets_excluded_by_csv_audit"], {
            "2026-11-10": excluded["2026-11-10"],
        })
        self.assertEqual(result["walkforward"]["target_missing_ranges"], [
            {"start_day": "2026-11-11", "end_day": "2026-11-12", "days": 2},
        ])
        self.assertEqual(result["walkforward"]["ranking"], [])

    def test_empty_source_reports_full_explicit_target_range(self):
        result = evaluate_csv_rows(
            [], evaluated_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
            target_from=date(2026, 10, 24), target_to=date(2026, 10, 26),
        )
        self.assertEqual(result["walkforward"]["target_missing_ranges"], [
            {"start_day": "2026-10-24", "end_day": "2026-10-26", "days": 3},
        ])
