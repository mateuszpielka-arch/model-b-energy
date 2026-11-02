"""Append-only scoring in isolated PostgreSQL staging; no production connection."""
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from shadow_postgres_archive import _guard
from shadow_scoring import SCORING_VERSION, _utc, expected_periods, score_day
from tge_parser_safe import parse_complete_standard_day

DDL = '''CREATE TABLE IF NOT EXISTS fix1_shadow_test.scores (
 forecast_id TEXT NOT NULL, model_version TEXT NOT NULL,
 batch_id BIGINT NOT NULL REFERENCES fix1_shadow_test.batches(id),
 scoring_version TEXT NOT NULL, input_sha256 TEXT NOT NULL,
 payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(forecast_id,model_version,batch_id,scoring_version)
)'''


def initialize_scores(conn):
    _guard(conn)
    with conn.transaction(): conn.execute(DDL)


def score_archived_day(conn, *, day, forecast, forecast_id, model_version,
                       forecast_status, issued_at, publication_ts, retrieved_at):
    """Validate archived actuals, calculate and persist an immutable score.

    Caller must supply immutable forecast inputs/provenance. No actual_verified
    flag from caller is accepted. The archived raw hash and every stored actual
    are rechecked. Standard 24h days only until source DST mapping is verified.
    """
    _guard(conn)
    forecast = list(forecast)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(716101,%s)',(day.toordinal(),))
        actual = conn.execute('''SELECT period_start_utc,price,batch_id
            FROM fix1_shadow_test.prices WHERE delivery_date=%s ORDER BY period_start_utc''',
            (day,)).fetchall()
        bids = {r[2] for r in actual}
        if len(actual) != 24 or len(bids) != 1:
            raise ValueError('One complete accepted actual batch required')
        bid = next(iter(bids))
        if conn.execute('SELECT 1 FROM fix1_shadow_test.quarantine WHERE batch_id=%s',(bid,)).fetchone():
            raise ValueError('Quarantined actual batch cannot be scored')
        raw_hash,ref_hash,parser,raw,delivery = conn.execute('''SELECT raw_sha256,
            reference_sha256,parser_version,raw_html,delivery_date
            FROM fix1_shadow_test.batches WHERE id=%s''',(bid,)).fetchone()
        if delivery != day or sha256(bytes(raw)).hexdigest() != raw_hash:
            raise ValueError('Archived evidence hash/date mismatch')
        parsed = parse_complete_standard_day(bytes(raw).decode('utf-8'), day.isoformat())
        expected = expected_periods(day)
        parsed_rows = [(expected[h-1], Decimal(str(parsed[h]))) for h in range(1,25)]
        actual_rows = [(p,v) for p,v,_ in actual]
        if actual_rows != parsed_rows:
            raise ValueError('Stored actual prices/periods differ from archived raw')
        version = ':'.join((day.isoformat(),raw_hash,ref_hash,parser))
        score = score_day(day=day,forecast=forecast,actual=actual_rows,
            forecast_id=forecast_id,model_version=model_version,actual_version=version,
            forecast_status=forecast_status,actual_verified=True,issued_at=issued_at,
            publication_ts=publication_ts,retrieved_at=retrieved_at)
        # Bind every forecast input, not just aggregate metrics. This detects a
        # changed forecast even when it happens to produce identical MAE/bias.
        inputs = dict(day=day.isoformat(),forecast_status=forecast_status,
            issued_at=_utc(issued_at).isoformat(),publication_ts=_utc(publication_ts).isoformat(),
            retrieved_at=_utc(retrieved_at).isoformat(),
            forecast=sorted((_utc(p).isoformat(),str(Decimal(str(v)))) for p,v in forecast))
        fingerprint = sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        payload = json.loads(json.dumps(asdict(score),default=str,sort_keys=True))
        key = (forecast_id,model_version,bid,SCORING_VERSION)
        from psycopg.types.json import Jsonb
        inserted = conn.execute('''INSERT INTO fix1_shadow_test.scores
            (forecast_id,model_version,batch_id,scoring_version,input_sha256,payload)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING forecast_id''',
            (*key,fingerprint,Jsonb(payload))).fetchone()
        if not inserted:
            previous = conn.execute('''SELECT input_sha256,payload FROM fix1_shadow_test.scores
                WHERE forecast_id=%s AND model_version=%s AND batch_id=%s AND scoring_version=%s''',key).fetchone()
            if previous != (fingerprint,payload):
                raise ValueError('Score identity conflict; existing result preserved')
    return ('INSERT' if inserted else 'REPLAY'), score
