"""Explicit opt-in PostgreSQL staging adapter. Never reads DATABASE_URL.

Only accepts a dedicated local test database. Not a production integration.
"""
from hashlib import sha256
from shadow_archive_batch import ArchiveBatch, classify_existing
from shadow_scoring import expected_periods

DDL = '''
CREATE SCHEMA IF NOT EXISTS fix1_shadow_test;
CREATE TABLE IF NOT EXISTS fix1_shadow_test.batches (
 id BIGSERIAL PRIMARY KEY, delivery_date DATE NOT NULL,
 raw_sha256 TEXT NOT NULL, reference_sha256 TEXT NOT NULL,
 parser_version TEXT NOT NULL, source_url TEXT NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL, raw_html BYTEA NOT NULL,
 UNIQUE(delivery_date, raw_sha256, reference_sha256, parser_version)
);
CREATE TABLE IF NOT EXISTS fix1_shadow_test.prices (
 delivery_date DATE NOT NULL, period_start_utc TIMESTAMPTZ NOT NULL,
 price NUMERIC NOT NULL, batch_id BIGINT NOT NULL REFERENCES fix1_shadow_test.batches(id),
 PRIMARY KEY(delivery_date, period_start_utc)
);
CREATE TABLE IF NOT EXISTS fix1_shadow_test.quarantine (
 batch_id BIGINT PRIMARY KEY REFERENCES fix1_shadow_test.batches(id),
 reason TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
'''


def connect_test_database(dsn):
    """Reject remote/production DSNs before connecting; no ambient credential lookup."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn)
    if (not info.get('dbname','').startswith('fix1_shadow_test') or
            info.get('host') not in ('127.0.0.1','localhost','::1')):
        raise ValueError('Explicit local fix1_shadow_test database required')
    return psycopg.connect(dsn, autocommit=True)


def _guard(conn):
    if (not conn.info.dbname.startswith('fix1_shadow_test') or
            conn.info.host not in ('127.0.0.1','localhost','::1')):
        raise ValueError('Only isolated local test database allowed')
    if not conn.autocommit:
        raise ValueError('Dedicated autocommit connection required for transaction ownership')


def initialize(conn):
    _guard(conn)
    with conn.transaction():
        conn.execute(DDL)


def archive_batch(conn, batch, raw):
    """Atomically insert a full day, replay, or quarantine; never UPDATE prices.

    All adapter writers lock the delivery date before reading existing prices.
    Unexpected external unique-key conflicts raise and roll back the whole batch.
    Quarantine retains raw HTML/reference hash for offline investigation.
    """
    _guard(conn)
    if not isinstance(batch, ArchiveBatch) or sha256(raw).hexdigest() != batch.raw_sha256:
        raise ValueError('Batch/raw evidence mismatch')
    if (len(batch.rows) != 24 or len(dict(batch.rows)) != 24 or
            set(dict(batch.rows)) != set(expected_periods(batch.delivery_date)) or
            any(not price.is_finite() for _,price in batch.rows)):
        raise ValueError('Complete finite standard-day batch required')
    with conn.transaction():
        # Stable numeric date key, no Python hash randomization across workers.
        conn.execute('SELECT pg_advisory_xact_lock(716101, %s)', (batch.delivery_date.toordinal(),))
        existing = conn.execute('SELECT period_start_utc,price FROM fix1_shadow_test.prices '
                                'WHERE delivery_date=%s ORDER BY period_start_utc',
                                (batch.delivery_date,)).fetchall()
        decision = classify_existing(batch, existing)
        conn.execute('''INSERT INTO fix1_shadow_test.batches
            (delivery_date,raw_sha256,reference_sha256,parser_version,source_url,retrieved_at,raw_html)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (batch.delivery_date,batch.raw_sha256,batch.reference_sha256,batch.parser_version,
             batch.source_url,batch.retrieved_at,raw))
        bid = conn.execute('''SELECT id FROM fix1_shadow_test.batches
            WHERE delivery_date=%s AND raw_sha256=%s AND reference_sha256=%s AND parser_version=%s''',
            batch.identity).fetchone()[0]
        if decision == 'QUARANTINE':
            conn.execute('INSERT INTO fix1_shadow_test.quarantine(batch_id,reason) VALUES (%s,%s) '
                         'ON CONFLICT DO NOTHING', (bid,'Existing prices differ or day is partial'))
        elif decision == 'INSERT':
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO fix1_shadow_test.prices '
                    '(delivery_date,period_start_utc,price,batch_id) VALUES (%s,%s,%s,%s)',
                    [(batch.delivery_date,p,v,bid) for p,v in batch.rows])
    return decision
