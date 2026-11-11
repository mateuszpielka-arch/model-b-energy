import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_asof import cutoff_for_day
from shadow_history_audit import read_csv
from shadow_walkforward import FROZEN_90D_VERSION
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

        cutoff = cutoff_for_day(target)
        record = dict(
            delivery_date=target.isoformat(), forecast_id="archived-test", forecast_status="FROZEN",
            model_version=FROZEN_90D_VERSION, issued_at=cutoff.isoformat(),
            publication_ts=(cutoff - timedelta(minutes=1)).isoformat(), retrieved_at=cutoff.isoformat(),
            prices=[(row["period_start_utc"].isoformat(), str(2 * row["jwcd_mw"] + 15))
                    for row in target_snapshot["rows"]],
        )
        with TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.csv"
            with history_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            frozen_path = Path(directory) / "frozen.json"
            command = [
                sys.executable, str(Path(__file__).resolve().parents[1] / "shadow_walkforward_csv.py"),
                "--evaluated-at", evaluated_at.isoformat(), "--target-from", target.isoformat(),
                "--frozen-90d-json", str(frozen_path), str(history_path),
            ]
            frozen_path.write_text(json.dumps([record]), encoding="utf-8")
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            exported = json.loads(completed.stdout)["walkforward"]
            benchmark = exported["frozen_90d_benchmark"]
            paired, = benchmark["paired_days"]
            self.assertEqual(paired["day"], target.isoformat())
            self.assertEqual(paired["frozen_90d"]["forecast_status"], "FROZEN")
            self.assertEqual(paired["frozen_90d"]["forecast_id"], "archived-test")
            self.assertEqual(paired["frozen_90d"]["model_version"], FROZEN_90D_VERSION)
            self.assertEqual(paired["frozen_90d"]["mae"], "5")
            self.assertEqual(paired["frozen_90d"]["periods"], 25)
            self.assertEqual(paired["frozen_90d"]["actual_version"], paired["ols90"]["actual_version"])
            accepted, = exported["accepted"]
            self.assertEqual(paired["ols90"], accepted["scores"]["ols90"])
            for score in accepted["scores"].values():
                self.assertEqual(score["forecast_status"], "SHADOW")
                self.assertEqual(score["periods"], 25)
                self.assertEqual(set(("mae", "mape_pct", "wape_pct", "bias")) - score.keys(), set())
            frozen, = [entry for entry in benchmark["ranking"] if entry["model"] == "frozen_90d"]
            self.assertEqual((frozen["periods"], frozen["mae"]), (25, "5"))
            self.assertEqual(benchmark["excluded_days"], {})

            frozen_path.write_text(json.dumps([record, record]), encoding="utf-8")
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("Duplicate archived FROZEN delivery date", rejected.stderr)

            for field in ("issued_at", "publication_ts", "retrieved_at"):
                with self.subTest(duplicate_field=field):
                    duplicate = json.dumps([record]).replace(
                        f'"{field}":',
                        f'"{field}": "{(cutoff + timedelta(seconds=1)).isoformat()}", "{field}":',
                        1,
                    )
                    frozen_path.write_text(duplicate, encoding="utf-8")
                    rejected = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(rejected.returncode, 2)
                    self.assertIn(f"Duplicate archived FROZEN field: {field}", rejected.stderr)

            period, price = record["prices"][0]
            bad_prices = [{period: None, price: None}, *record["prices"][1:]]
            frozen_path.write_text(json.dumps([{**record, "prices": bad_prices}]), encoding="utf-8")
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("Invalid archived FROZEN forecast at entry 1", rejected.stderr)

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

    def test_snapshot_identifier_type_gates_admission_before_ranking(self):
        day = date(2026, 11, 11)
        cutoff = cutoff_for_day(day)
        fixture = Path(__file__).parent / "fixtures/tge_delivery_2026-11-11/tge_delivery_2026-11-11.csv"
        rows = read_csv(fixture)
        for row in rows:
            row.update(
                actual_verified="true", actual_version="test-reference",
                actual_available_at=(cutoff + timedelta(hours=3)).isoformat(),
                jwcd_mw="10000", jwcd_snapshot_id=42,
                publication_ts=(cutoff - timedelta(minutes=1)).isoformat(),
                retrieved_at=cutoff.isoformat(), retrieval_completed_at=cutoff.isoformat(),
            )
        evaluated_at = cutoff + timedelta(days=2)
        valid = evaluate_csv_rows(rows, evaluated_at=evaluated_at)
        self.assertEqual(valid["csv_admission"]["admitted_days"], 1)
        self.assertEqual(valid["walkforward"]["requested_days"], 1)

        for identifier in (True, 4.2, ["snapshot"], {"id": "snapshot"}, " "):
            with self.subTest(identifier=identifier):
                invalid_rows = [{**row, "jwcd_snapshot_id": identifier} for row in rows]
                result = evaluate_csv_rows(invalid_rows, evaluated_at=evaluated_at)
                self.assertEqual(result["history_audit"]["days"][0]["availability_proven_rows"], 24)
                self.assertEqual(result["csv_admission"]["excluded_days"], {
                    day.isoformat(): ["MISSING_JWCD_SNAPSHOT_ID"],
                })
                self.assertEqual(result["walkforward"]["requested_days"], 0)
                self.assertEqual(result["walkforward"]["ranking"], [])

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
