"""Prepare immutable, evidence-linked Fixing I batches; no DB/network writes.

Admission is restricted to standard 24h days. DST requires verified source labels.
A batch is a staging candidate, not a certification of external TGE authenticity.
"""
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from urllib.parse import urlsplit, parse_qs
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import re
from shadow_asof import cutoff_for_day
from shadow_history_audit import _stamp, read_csv_rows
from shadow_scoring import expected_periods
from tge_parser_safe import parse_complete_standard_day

SOURCE_URL = 'https://tge.pl/energia-elektryczna-rdn'
PARSER_VERSION = 'rdn-layout-shadow-v2'
MAX_REFERENCE_BYTES = 64 * 1024


@dataclass(frozen=True)
class ArchiveBatch:
    delivery_date: date
    raw_sha256: str
    reference_sha256: str
    retrieved_at: datetime
    rows: tuple
    source_url: str = SOURCE_URL
    parser_version: str = PARSER_VERSION

    @property
    def identity(self):
        return (self.delivery_date.isoformat(), self.raw_sha256,
                self.reference_sha256, self.parser_version)


def read_reference_csv(reference, day):
    """Read reference pairs from the original bytes, preserving duplicate hours."""
    if len(reference) > MAX_REFERENCE_BYTES:
        raise ValueError('Fix1 reference CSV exceeds 64 KiB')
    with StringIO(reference.decode('utf-8-sig'), newline='') as stream:
        try:
            rows = read_csv_rows(stream, source='Fix1 reference CSV')
        except csv.Error as exc:
            raise ValueError('Malformed Fix1 reference CSV') from exc
    if rows and not {'delivery_date', 'hour', 'price_pln_mwh'} <= rows[0].keys():
        raise ValueError('Missing required Fix1 reference CSV columns')
    if any(row['delivery_date'] != day for row in rows):
        raise ValueError('Reference delivery date mismatch')
    if rows and 'period_start_utc' in rows[0]:
        periods = {
            f'H{hour:02d}': period
            for hour, period in enumerate(expected_periods(date.fromisoformat(day)), 1)
        }
        for row in rows:
            label = row['hour']
            try:
                period = _stamp(row['period_start_utc'])
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid Fix1 reference UTC period for {label}') from exc
            if periods.get(label) != period:
                raise ValueError(f'Fix1 reference UTC period does not match {label}')
    return [(row['hour'], row['price_pln_mwh']) for row in rows]


def validate_source_url(source_url: str, day: str):
    """Check the official RDN endpoint and any declared historical session."""
    url = urlsplit(source_url)
    if (url.scheme != 'https' or url.netloc != 'tge.pl' or
            url.path != '/energia-elektryczna-rdn' or url.fragment):
        raise ValueError('Unexpected source URL')
    if url.query:
        query = parse_qs(url.query, keep_blank_values=True)
        session = (date.fromisoformat(day)-timedelta(days=1)).strftime('%d-%m-%Y')
        if query != {'dateShow': [session]}:
            raise ValueError('Historical session URL does not match delivery date')


def prepare_batch(*, raw: bytes, day: str, source_url: str, retrieved_at: datetime,
                  reference_rows, reference_sha256: str):
    """Bind complete parsed prices to an independent reference artifact hash.

    Reference rows are (H01..H24, Decimal-compatible price) pairs. Preserve
    duplicates for detection. Caller retains raw/reference bytes immutably.
    """
    validate_source_url(source_url, day)
    if not isinstance(raw, bytes) or not raw:
        raise ValueError('Nonempty raw HTTP bytes required')
    if (not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None
            or retrieved_at.utcoffset() is None):
        raise ValueError('Aware retrieval timestamp required')
    d = date.fromisoformat(day)
    retrieved_at = retrieved_at.astimezone(timezone.utc)
    if retrieved_at <= cutoff_for_day(d):
        raise ValueError('Fix1 retrieval must be after D-1 09:50 Europe/Warsaw')
    if not re.fullmatch('[0-9a-f]{64}', reference_sha256):
        raise ValueError('Independent reference artifact SHA256 required')
    parsed = parse_complete_standard_day(raw.decode('utf-8', errors='strict'), day)
    reference = {}
    for label, value in reference_rows:
        if not re.fullmatch(r'H(?:0[1-9]|1[0-9]|2[0-4])', label):
            raise ValueError('Invalid reference hour')
        if label in reference:
            raise ValueError('Duplicate reference hour')
        try:
            if isinstance(value, bool):
                raise ValueError('Boolean price')
            price = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError('Invalid reference price') from exc
        if not price.is_finite():
            raise ValueError('Nonfinite reference price')
        reference[label] = price
    expected = {f'H{h:02d}' for h in range(1,25)}
    if set(reference) != expected:
        raise ValueError('Incomplete reference day')
    prices = {f'H{h:02d}': Decimal(str(v)) for h,v in parsed.items()}
    if prices != reference:
        mismatches = sorted(k for k in expected if prices[k] != reference[k])
        raise ValueError(f'Official reference mismatch: {mismatches}')
    periods = expected_periods(d)
    return ArchiveBatch(d, sha256(raw).hexdigest(), reference_sha256,
                        retrieved_at,
                        tuple((periods[h-1], reference[f'H{h:02d}']) for h in range(1,25)),
                        source_url=source_url)


def classify_existing(batch: ArchiveBatch, existing_rows):
    """Plan insert/replay/quarantine without mutating any existing records.

    A DB adapter must serialize this decision with the write in one transaction.
    Partial existing days and changed prices are quarantined, never repaired here.
    """
    old = {}
    for period, value in existing_rows:
        if period.tzinfo is None or period.utcoffset() is None:
            raise ValueError('Existing period must include timezone')
        key = period.astimezone(timezone.utc)
        if key in old:
            return 'QUARANTINE'
        try:
            price = Decimal(str(value))
        except InvalidOperation:
            return 'QUARANTINE'
        if not price.is_finite():
            return 'QUARANTINE'
        old[key] = price
    if not old:
        return 'INSERT'
    return 'REPLAY' if old == dict(batch.rows) else 'QUARANTINE'


def revalidate_batch(batch, raw):
    """Rebuild admission from original HTML at the writer boundary.

    Frozen dataclasses do not prove correctness: callers can construct/replace
    them directly. Reference hash authenticity remains caller-owned evidence;
    every price, period, date, source URL and supported parser version is checked.
    """
    from shadow_scoring import _prices
    if not isinstance(batch, ArchiveBatch) or not isinstance(raw, bytes):
        raise ValueError('Expected ArchiveBatch and original raw bytes')
    if sha256(raw).hexdigest() != batch.raw_sha256:
        raise ValueError('Batch/raw evidence mismatch')
    if batch.parser_version != PARSER_VERSION:
        raise ValueError('Unsupported admission parser version')
    periods = expected_periods(batch.delivery_date)
    prices = _prices(batch.rows)
    if len(periods) != 24 or set(prices) != set(periods):
        raise ValueError('Complete standard-day periods required')
    return prepare_batch(raw=raw, day=batch.delivery_date.isoformat(),
        source_url=batch.source_url, retrieved_at=batch.retrieved_at,
        reference_rows=[(f'H{i:02d}',prices[period]) for i,period in enumerate(periods,1)],
        reference_sha256=batch.reference_sha256)
