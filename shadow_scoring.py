"""Pure SHADOW scoring. No database, network, or production model imports.

Inputs must already have independently verified actual provenance and UTC period
mapping. This module does not certify either; it refuses unverified batches.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

SCORING_VERSION = 'fix1-shadow-score-v1'
UTC = timezone.utc
WARSAW = ZoneInfo('Europe/Warsaw')


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamp must include timezone')
    return value.astimezone(UTC)


def expected_periods(day: date):
    """UTC starts of all hourly periods in a Warsaw delivery day (23/24/25)."""
    start = datetime.combine(day, time(), WARSAW).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(), WARSAW).astimezone(UTC)
    return tuple(start + timedelta(hours=i) for i in range(int((end-start).total_seconds()/3600)))


def _prices(rows):
    # Sequence of pairs, not dict: duplicate periods must remain detectable.
    out = {}
    try:
        records = iter(rows)
    except TypeError as exc:
        raise ValueError('Invalid price collection') from exc
    for record in records:
        try:
            period, price = record
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid price row') from exc
        key = _utc(period)
        if key in out:
            raise ValueError('Duplicate UTC delivery period')
        try:
            if isinstance(price, bool):
                raise ValueError('Boolean is not a price')
            value = Decimal(str(price))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError('Invalid price') from exc
        if not value.is_finite():
            raise ValueError('Non-finite price')
        out[key] = value
    return out


@dataclass(frozen=True)
class Score:
    forecast_id: str
    model_version: str
    actual_version: str
    forecast_status: str
    periods: int
    mape_periods: int
    zero_actual_periods: int
    mae: Decimal
    mape_pct: Decimal | None
    wape_pct: Decimal | None
    bias: Decimal
    scoring_version: str = SCORING_VERSION

    @property
    def identity(self):
        return (self.forecast_id, self.model_version, self.actual_version, self.scoring_version)


def score_day(*, day, forecast, actual, forecast_id, model_version, actual_version,
              forecast_status, actual_verified, issued_at, publication_ts, retrieved_at):
    """Score one complete day, with all forecast inputs available by D-1 09:50.

    publication_ts is the latest publication timestamp across the forecast's JWCD
    inputs; retrieved_at is their latest retrieval timestamp. Callers must supply
    these from immutable provenance, not current or inferred timestamps.
    Zero actuals are excluded only from MAPE, with explicit coverage counts.
    """
    if actual_verified is not True:
        raise ValueError('Unverified actual batch')
    if any(not isinstance(v, str) or not v.strip() for v in
           (forecast_id, model_version, actual_version)):
        raise ValueError('Explicit forecast/model/actual identities required')
    if forecast_status not in ('FROZEN', 'SHADOW'):
        raise ValueError('Only FROZEN or SHADOW forecasts can be scored')
    issued, published, retrieved = map(_utc, (issued_at, publication_ts, retrieved_at))
    cutoff = datetime.combine(day - timedelta(days=1), time(9,50), WARSAW).astimezone(UTC)
    if not published <= retrieved <= issued <= cutoff:
        raise ValueError('Invalid chronology or data unavailable before D-1 09:50')
    fp, ap = _prices(forecast), _prices(actual)
    expected = set(expected_periods(day))
    if set(fp) != expected or set(ap) != expected:
        raise ValueError('Incomplete or mismatched delivery periods')
    with localcontext() as ctx:
        ctx.prec = 34
        errors = [fp[p] - ap[p] for p in sorted(expected)]
        abs_sum = sum(map(abs, errors), Decimal(0))
        denom = sum(map(abs, ap.values()), Decimal(0))
        ratios = [abs(fp[p]-ap[p])/abs(ap[p]) for p in sorted(expected) if ap[p] != 0]
        n = len(expected)
        return Score(forecast_id, model_version, actual_version, forecast_status,
                     n, len(ratios), n-len(ratios), abs_sum/n,
                     100*sum(ratios)/len(ratios) if ratios else None,
                     100*abs_sum/denom if denom else None,
                     sum(errors)/n)
