"""Explicit opt-in PostgreSQL staging adapter. Never reads DATABASE_URL.

Only accepts a dedicated local test database. Not a production integration.
"""
from decimal import Decimal
from hashlib import sha256

from shadow_archive_batch import classify_existing, revalidate_batch
from tge_parser_safe import parse_complete_standard_day

LOOPBACK_ADDRESSES = ('127.0.0.1', '::1')
LOCAL_HOSTS = (*LOOPBACK_ADDRESSES, 'localhost')
# Delivery-day ordinals start at one.
_SCHEMA_INIT_LOCK_KEY = 0

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
    """Connect only to a loopback address in the dedicated test database."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn)
    host = info.get('host')
    address = info.get('hostaddr') or ('127.0.0.1' if host == 'localhost' else host)
    if (not info.get('dbname','').startswith('fix1_shadow_test') or
            host not in LOCAL_HOSTS or address not in LOOPBACK_ADDRESSES):
        raise ValueError('Explicit local fix1_shadow_test database required')
    # An explicit address prevents PGHOSTADDR or name resolution rerouting the connection.
    return psycopg.connect(dsn, hostaddr=address, autocommit=True)


def _guard(conn):
    if (not conn.info.dbname.startswith('fix1_shadow_test') or
            conn.info.host not in LOCAL_HOSTS or conn.info.hostaddr not in LOOPBACK_ADDRESSES):
        raise ValueError('Only isolated local test database allowed')
    if not conn.autocommit:
        raise ValueError('Dedicated autocommit connection required for transaction ownership')


def _lock_delivery_day(conn, day):
    # Reads after the lock must see writes committed while waiting for it.
    isolation = conn.execute('SHOW transaction_isolation').fetchone()[0]
    if isolation != 'read committed':
        raise ValueError('Fix1 day lock requires READ COMMITTED transaction isolation')
    _lock_archive_key(conn, day.toordinal())


def _lock_archive_key(conn, key):
    # Bound waits within the test transaction, preserving a stricter caller timeout.
    conn.execute("""
        SELECT set_config('lock_timeout', '5000', true)
        FROM (SELECT setting FROM pg_settings WHERE name = 'lock_timeout' LIMIT 1) AS timeout
        WHERE setting::int = 0 OR setting::int > 5000
    """)
    conn.execute('SELECT pg_advisory_xact_lock(716101, %s)', (key,))


def _has_delivery_quarantine(conn, day):
    return conn.execute(
        """SELECT EXISTS (
               SELECT 1
               FROM fix1_shadow_test.quarantine AS quarantine
               JOIN fix1_shadow_test.batches AS batch ON batch.id = quarantine.batch_id
               WHERE batch.delivery_date = %s
           )""",
        (day,),
    ).fetchone()[0]


def initialize(conn):
    _guard(conn)
    with conn.transaction():
        ready = conn.execute('''
            SELECT to_regclass('fix1_shadow_test.batches') IS NOT NULL
               AND to_regclass('fix1_shadow_test.prices') IS NOT NULL
               AND to_regclass('fix1_shadow_test.quarantine') IS NOT NULL
        ''').fetchone()[0]
        if not ready:
            _lock_archive_key(conn, _SCHEMA_INIT_LOCK_KEY)
            conn.execute(DDL)


def archive_batch(conn, batch, raw):
    """Atomically insert a full day, replay, or quarantine; never UPDATE prices.

    All adapter writers lock the delivery date in READ COMMITTED before
    reading existing prices.
    Unexpected external unique-key conflicts raise and roll back the whole batch.
    Quarantine retains raw HTML/reference hash for offline investigation.
    """
    _guard(conn)
    batch = revalidate_batch(batch, raw)
    with conn.transaction():
        _lock_delivery_day(conn, batch.delivery_date)
        existing = conn.execute('''
            SELECT price.period_start_utc, price.price, price.batch_id, source.delivery_date
            FROM fix1_shadow_test.prices AS price
            JOIN fix1_shadow_test.batches AS source ON source.id = price.batch_id
            WHERE price.delivery_date = %s ORDER BY price.period_start_utc
        ''', (batch.delivery_date,)).fetchall()
        source_batches = {(batch_id, day) for _, _, batch_id, day in existing}
        if len(source_batches) > 1 or any(day != batch.delivery_date for _, day in source_batches):
            decision = 'QUARANTINE'
            reason = 'Existing prices do not point to one batch for the delivery day'
        else:
            decision = classify_existing(batch, [(period, price) for period, price, _, _ in existing])
            reason = 'Existing prices differ or day is partial'
        if decision == 'REPLAY':
            if _has_delivery_quarantine(conn, batch.delivery_date):
                return 'QUARANTINE'
            (source_id, _), = source_batches
            stored_hash, stored_raw = conn.execute(
                'SELECT raw_sha256, raw_html FROM fix1_shadow_test.batches WHERE id = %s',
                (source_id,),
            ).fetchone()
            if sha256(stored_raw).hexdigest() != stored_hash:
                decision = 'QUARANTINE'
                reason = 'Existing price batch raw HTML hash mismatch'
            elif stored_hash != batch.raw_sha256:
                try:
                    archived_prices = parse_complete_standard_day(
                        bytes(stored_raw).decode('utf-8'), batch.delivery_date.isoformat(),
                    )
                except ValueError:
                    decision = 'QUARANTINE'
                    reason = 'Existing price batch raw HTML cannot be parsed'
                else:
                    expected = tuple(Decimal(str(archived_prices[hour])) for hour in range(1, 25))
                    if expected != tuple(price for _, price in batch.rows):
                        decision = 'QUARANTINE'
                        reason = 'Existing price batch raw HTML disagrees with stored prices'
        if decision == 'INSERT':
            if _has_delivery_quarantine(conn, batch.delivery_date):
                decision = 'QUARANTINE'
                reason = 'Delivery day is already quarantined'
            elif conn.execute(
                'SELECT EXISTS (SELECT 1 FROM fix1_shadow_test.batches WHERE delivery_date = %s)',
                (batch.delivery_date,),
            ).fetchone()[0]:
                decision = 'QUARANTINE'
                reason = 'Archived batch exists but delivery day has no prices'
        conn.execute('''INSERT INTO fix1_shadow_test.batches
            (delivery_date,raw_sha256,reference_sha256,parser_version,source_url,retrieved_at,raw_html)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (batch.delivery_date,batch.raw_sha256,batch.reference_sha256,batch.parser_version,
             batch.source_url,batch.retrieved_at,raw))
        bid, quarantined = conn.execute('''
            SELECT batch.id, EXISTS (
                SELECT 1 FROM fix1_shadow_test.quarantine AS q WHERE q.batch_id = batch.id
            )
            FROM fix1_shadow_test.batches AS batch
            WHERE delivery_date=%s AND raw_sha256=%s AND reference_sha256=%s AND parser_version=%s
        ''', batch.identity).fetchone()
        if quarantined:
            return 'QUARANTINE'
        if decision == 'QUARANTINE':
            conn.execute('INSERT INTO fix1_shadow_test.quarantine(batch_id,reason) VALUES (%s,%s) '
                         'ON CONFLICT DO NOTHING', (bid, reason))
        elif decision == 'INSERT':
            with conn.cursor() as cur:
                cur.executemany('INSERT INTO fix1_shadow_test.prices '
                    '(delivery_date,period_start_utc,price,batch_id) VALUES (%s,%s,%s,%s)',
                    [(batch.delivery_date,p,v,bid) for p,v in batch.rows])
    return decision
