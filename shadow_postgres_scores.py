"""Append-only scoring in isolated PostgreSQL staging; no production connection."""
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from shadow_archive_batch import PARSER_VERSION, prepare_batch, read_reference_csv, validate_source_url
from shadow_asof import cutoff_for_day
from shadow_postgres_archive import (
    _SCHEMA_INIT_LOCK_KEY, _guard, _has_delivery_quarantine, _lock_archive_key, _lock_delivery_day,
)
from shadow_scoring import (
    SCORING_VERSION, _prices, _utc, expected_periods, score_day,
    validate_forecast_identity, validate_forecast_timing,
)
from tge_parser_safe import parse_complete_standard_day

DDL = '''CREATE TABLE IF NOT EXISTS fix1_shadow_test.scores (
 forecast_id TEXT NOT NULL, model_version TEXT NOT NULL,
 batch_id BIGINT NOT NULL REFERENCES fix1_shadow_test.batches(id),
 scoring_version TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(forecast_id,model_version,batch_id,scoring_version)
)'''


def canonical_decimal(value):
    """Serialize an already validated Decimal without rounding or signed zero."""
    if not value:
        return '0'
    sign, digits, exponent = value.as_tuple()
    last = len(digits)
    while digits[last - 1] == 0:
        last -= 1
    return str(Decimal((sign, digits[:last], exponent + len(digits) - last))).upper()


def input_fingerprint(inputs):
    return sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _score_record(metadata, forecast_prices, score_values, decimal_text):
    inputs = {**metadata, 'forecast': sorted(
        (period.isoformat(), decimal_text(price)) for period, price in forecast_prices.items()
    )}
    payload = json.loads(json.dumps(score_values, default=decimal_text, sort_keys=True))
    return input_fingerprint(inputs), payload


def initialize_scores(conn):
    _guard(conn)
    with conn.transaction():
        exists = conn.execute(
            "SELECT to_regclass('fix1_shadow_test.scores') IS NOT NULL"
        ).fetchone()[0]
        if not exists:
            isolation = conn.execute('SHOW transaction_isolation').fetchone()[0]
            if isolation != 'read committed':
                raise ValueError('Fix1 score initialization requires READ COMMITTED')
            _lock_archive_key(conn, _SCHEMA_INIT_LOCK_KEY)
            conn.execute(DDL)


def score_archived_day(conn, *, day, forecast, forecast_id, model_version,
                       forecast_status, issued_at, publication_ts, retrieved_at, reference_csv=None,
                       evaluated_at=None):
    """Validate archived actuals, calculate and persist an immutable score.

    Caller must supply immutable forecast inputs/provenance. No actual_verified
    flag from caller is accepted. The archived raw hash and every stored actual
    are rechecked. Supplying the original reference CSV also verifies its
    archived hash and hourly prices. The archived capture must follow
    issuance and D-1 09:50 Warsaw. If evaluated_at is supplied, the capture
    must be available by that time. Current quarantine checks still apply. Standard 24h days only.
    Scoring requires a READ COMMITTED transaction.
    """
    _guard(conn)
    validate_forecast_identity(forecast_id, model_version, forecast_status)
    validate_forecast_timing(day, issued_at, publication_ts, retrieved_at)
    evaluation_time = _utc(evaluated_at) if evaluated_at is not None else None
    periods = expected_periods(day)
    if len(periods) != 24:
        raise ValueError('Archived Fix1 scoring requires a standard 24-hour delivery day')
    forecast_prices = _prices(forecast)
    if set(forecast_prices) != set(periods):
        raise ValueError('Incomplete or mismatched delivery periods')
    with conn.transaction():
        _lock_delivery_day(conn, day)
        actual = conn.execute('''SELECT period_start_utc,price,batch_id
            FROM fix1_shadow_test.prices WHERE delivery_date=%s ORDER BY period_start_utc''',
            (day,)).fetchall()
        bids = {r[2] for r in actual}
        if len(actual) != 24 or len(bids) != 1:
            raise ValueError('One complete accepted actual batch required')
        bid = next(iter(bids))
        if _has_delivery_quarantine(conn, day):
            raise ValueError('Quarantined delivery day cannot be scored')
        raw_hash,ref_hash,parser,raw,delivery,source_url,captured_at = conn.execute('''SELECT raw_sha256,
            reference_sha256,parser_version,raw_html,delivery_date,source_url,
            CASE WHEN retrieved_at >= TIMESTAMPTZ '0001-01-01 00:00:00+00'
                   AND retrieved_at < TIMESTAMPTZ '10000-01-01 00:00:00+00'
                 THEN retrieved_at AT TIME ZONE 'UTC' END
            FROM fix1_shadow_test.batches WHERE id=%s''',(bid,)).fetchone()
        raw = bytes(raw)
        if delivery != day or sha256(raw).hexdigest() != raw_hash:
            raise ValueError('Archived evidence hash/date mismatch')
        if captured_at is None:
            raise ValueError('Archived Fix1 capture timestamp is outside the supported UTC range')
        capture_time = captured_at.replace(tzinfo=timezone.utc)
        if capture_time <= _utc(issued_at):
            raise ValueError('Archived Fix1 capture must be after forecast issuance')
        if capture_time <= cutoff_for_day(day):
            raise ValueError('Archived Fix1 capture must be after D-1 09:50 Europe/Warsaw')
        if evaluation_time is not None and capture_time > evaluation_time:
            raise ValueError('Archived Fix1 capture not yet available at evaluation time')
        if not re.fullmatch(r'[0-9a-f]{64}', ref_hash):
            raise ValueError('Invalid archived reference SHA-256')
        if parser != PARSER_VERSION:
            raise ValueError('Unsupported archived parser version')
        if reference_csv is None:
            validate_source_url(source_url, day.isoformat())
            parsed = parse_complete_standard_day(raw.decode('utf-8'), day.isoformat())
            parsed_rows = [(period, Decimal(str(parsed[hour])))
                           for hour, period in enumerate(periods, 1)]
        else:
            if not isinstance(reference_csv, bytes) or sha256(reference_csv).hexdigest() != ref_hash:
                raise ValueError('Archived reference CSV hash mismatch')
            verified_batch = prepare_batch(
                raw=raw, day=day.isoformat(), source_url=source_url, retrieved_at=capture_time,
                reference_rows=read_reference_csv(reference_csv, day.isoformat()),
                reference_sha256=ref_hash,
            )
            parsed_rows = list(verified_batch.rows)
        actual_rows = [(p,v) for p,v,_ in actual]
        if actual_rows != parsed_rows:
            raise ValueError('Stored actual prices/periods differ from archived raw')
        version = ':'.join((day.isoformat(),raw_hash,ref_hash,parser))
        score = score_day(day=day,forecast=forecast_prices.items(),actual=actual_rows,
            forecast_id=forecast_id,model_version=model_version,actual_version=version,
            forecast_status=forecast_status,actual_verified=True,issued_at=issued_at,
            publication_ts=publication_ts,retrieved_at=retrieved_at)
        # Bind every forecast input, not just aggregate metrics. This detects a
        # changed forecast even when it happens to produce identical MAE/bias.
        metadata = dict(day=day.isoformat(), forecast_status=forecast_status,
            issued_at=_utc(issued_at).isoformat(), publication_ts=_utc(publication_ts).isoformat(),
            retrieved_at=_utc(retrieved_at).isoformat())
        score_values = asdict(score)
        fingerprint, payload = _score_record(metadata, forecast_prices, score_values, canonical_decimal)
        key = (forecast_id,model_version,bid,SCORING_VERSION)
        from psycopg.types.json import Jsonb
        inserted = conn.execute('''INSERT INTO fix1_shadow_test.scores
            (forecast_id,model_version,batch_id,scoring_version,input_sha256,payload)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING forecast_id''',
            (*key,fingerprint,Jsonb(payload))).fetchone()
        if not inserted:
            previous = conn.execute('''SELECT input_sha256,payload FROM fix1_shadow_test.scores
                WHERE forecast_id=%s AND model_version=%s AND batch_id=%s AND scoring_version=%s''',key).fetchone()
            if previous is None:
                raise ValueError('Score identity conflict: existing result unavailable')
            if previous != (fingerprint, payload):
                # Older rows may use lowercase exponents or preserve Decimal spelling.
                previous_formats = (
                    lambda value: canonical_decimal(value).lower(),
                    lambda value: str(value).upper(),
                    lambda value: str(value).lower(),
                )
                known_fingerprints = {fingerprint}
                for decimal_text in previous_formats:
                    candidate = _score_record(metadata, forecast_prices, score_values, decimal_text)
                    if previous == candidate:
                        break
                    known_fingerprints.add(candidate[0])
                else:
                    reason = (
                        'stored score payload differs'
                        if previous[0] in known_fingerprints
                        else 'input fingerprint differs'
                    )
                    raise ValueError(f'Score identity conflict: {reason}; existing result preserved')
    return ('INSERT' if inserted else 'REPLAY'), score
