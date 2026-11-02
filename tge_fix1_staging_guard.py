"""Fail-closed SHADOW validation for TGE Fixing I. No network or database writes.

Checks structure only. Real raw TGE HTML has not yet been tested.
"""
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo
from tge_parser_safe import _Rows, parse_fix1

WARSAW = ZoneInfo("Europe/Warsaw")

def validate_staging_html(html: str, delivery_date: str, reference=None) -> dict[int, float]:
    day = date.fromisoformat(delivery_date)
    next_day = day + timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=WARSAW)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=WARSAW)
    hours = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600
    if hours != 24:
        raise ValueError(f"DST day has {hours:g} periods; H01-H24 schema is unsafe")
    parser = _Rows()
    parser.feed(html)
    headers = [row for row in parser.rows if len(row) >= 3
               and row[0].casefold().strip() == "data dostawy"
               and row[1].casefold().strip() == "typ instrumentu"
               and "kurs" in row[2].casefold() and "pln/mwh" in row[2].casefold()]
    if len(headers) != 1 or "fixing i" not in html.casefold():
        raise ValueError("Fixing I header / first three column identities not confirmed")
    prices = parse_fix1(html, delivery_date)
    if set(prices) != set(range(1, 25)):
        raise ValueError(f"Incomplete/duplicate hourly set: {len(prices)}/24")
    if not all(isfinite(v) and -10000 <= v <= 20000 for v in prices.values()):
        raise ValueError("Non-finite/out-of-range hourly price")
    if sum(v in (60.0, 2026.0) for v in prices.values()) >= 8:
        raise ValueError("Suspect instrument/year values in price column")
    if reference is not None:
        if set(reference) != set(range(1, 25)):
            raise ValueError("Reference does not contain 24 hours")
        mismatches = [h for h in range(1, 25) if abs(prices[h] - float(reference[h])) > 0.005]
        if mismatches:
            raise ValueError(f"Mismatch against independently verified TGE reference: {mismatches}")
    return prices
