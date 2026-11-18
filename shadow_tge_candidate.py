"""Read-only SHADOW TGE candidate: never writes to PostgreSQL or scores forecasts.

Usage: python shadow_tge_candidate.py YYYY-MM-DD saved_tge_page.html
Requires official raw HTML. A complete ordinary (non-DST) day is mandatory.
"""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from tge_parser_safe import parse_complete_standard_day
from shadow_scoring import expected_periods


def validate_candidate(day: str, html: bytes) -> dict:
    date.fromisoformat(day)
    prices = parse_complete_standard_day(html.decode("utf-8-sig"), day, exact=True)
    return {
        "delivery_date": day,
        "status": "SHADOW_VALIDATED_LAYOUT_ONLY",
        "row_count": len(prices),
        "sha256": hashlib.sha256(html).hexdigest(),
        "prices": {f"H{h:02d}": str(v) for h, v in sorted(prices.items())},
        "periods": [
            {"tge_hour_label": f"H{hour:02d}", "period_start_utc": period.isoformat(),
             "price_pln_mwh": str(prices[hour])}
            for hour, period in enumerate(expected_periods(date.fromisoformat(day)), 1)
        ],
        "archive_written": False,
        "scoring_written": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("html_file", type=Path)
    args = ap.parse_args()
    result = validate_candidate(args.day, args.html_file.read_bytes())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
