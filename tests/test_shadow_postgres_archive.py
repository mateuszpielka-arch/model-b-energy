"""Integration tests use only an explicitly supplied local disposable database."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch
from shadow_postgres_archive import connect_test_database, initialize, archive_batch
import test_shadow_archive_batch as fixtures
from shadow_archive_batch import prepare_batch
from shadow_asof import cutoff_for_day
from shadow_ingest_manifest import load_batches

DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')

class PostgresConnectionGuardTests(unittest.TestCase):
    def test_claimed_local_host_with_nonlocal_peer_is_rejected_before_sql(self):
        conn = Mock()
        conn.info.dbname = 'fix1_shadow_test'
        conn.info.host = 'localhost'
        conn.info.hostaddr = '192.0.2.1'
        with self.assertRaisesRegex(ValueError, 'Only isolated local'):
            initialize(conn)
        conn.transaction.assert_not_called()


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class PostgresArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN); initialize(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.quarantine,fix1_shadow_test.prices,fix1_shadow_test.batches RESTART IDENTITY CASCADE')
        kw=fixtures.ArchiveAdmissionTests().args(); self.raw=kw['raw']; self.batch=prepare_batch(**kw)

    def tearDown(self): self.conn.close()

    def count(self,table):
        # table values are constants from tests, never user input.
        return self.conn.execute('SELECT count(*) FROM fix1_shadow_test.'+table).fetchone()[0]

    def test_full_day_and_identical_replay(self):
        self.assertEqual(archive_batch(self.conn,self.batch,self.raw),'INSERT')
        self.assertEqual(archive_batch(self.conn,self.batch,self.raw),'REPLAY')
        self.assertEqual(self.count('prices'),24); self.assertEqual(self.count('batches'),1)

    def test_rejects_remote_hostaddr_before_connecting(self):
        dsn = 'host=localhost hostaddr=192.0.2.1 dbname=fix1_shadow_test'
        with patch('psycopg.connect') as connect:
            with self.assertRaisesRegex(ValueError, 'Explicit local'):
                connect_test_database(dsn)
            connect.assert_not_called()

    def test_ambient_hostaddr_cannot_override_local_dsn(self):
        with patch.dict(os.environ, {'PGHOSTADDR': '192.0.2.1'}):
            with connect_test_database(DSN) as conn:
                self.assertIn(conn.info.hostaddr, ('127.0.0.1', '::1'))
                self.assertEqual(conn.execute('SELECT 1').fetchone(), (1,))

    def test_conflicting_day_quarantined_without_overwrite(self):
        archive_batch(self.conn,self.batch,self.raw)
        # Simulate an existing bad archive row in this disposable test DB only.
        self.conn.execute('UPDATE fix1_shadow_test.prices SET price=60 WHERE period_start_utc=%s',
                          (self.batch.rows[0][0],))
        self.assertEqual(archive_batch(self.conn,self.batch,self.raw),'QUARANTINE')
        self.assertEqual(self.count('quarantine'),1)
        self.assertEqual(self.conn.execute('SELECT price FROM fix1_shadow_test.prices ORDER BY period_start_utc LIMIT 1').fetchone()[0],60)

    def test_partial_existing_day_not_silently_repaired(self):
        archive_batch(self.conn,self.batch,self.raw)
        self.conn.execute('DELETE FROM fix1_shadow_test.prices WHERE period_start_utc=%s',(self.batch.rows[0][0],))
        self.assertEqual(archive_batch(self.conn,self.batch,self.raw),'QUARANTINE')
        self.assertEqual(self.count('prices'),23)

    def test_matching_prices_with_mixed_source_batches_are_quarantined(self):
        self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'INSERT')
        second_id, = self.conn.execute('''
            INSERT INTO fix1_shadow_test.batches
                (delivery_date, raw_sha256, reference_sha256, parser_version,
                 source_url, retrieved_at, raw_html)
            SELECT delivery_date, raw_sha256, %s, parser_version,
                   source_url, retrieved_at, raw_html
            FROM fix1_shadow_test.batches RETURNING id
        ''', ('a' * 64,)).fetchone()
        self.conn.execute('UPDATE fix1_shadow_test.prices SET batch_id=%s WHERE period_start_utc=%s',
                          (second_id, self.batch.rows[0][0]))
        query = 'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        before = self.conn.execute(query).fetchall()
        for _ in range(2):
            self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'QUARANTINE')
        self.assertEqual(self.conn.execute(query).fetchall(), before)
        self.assertEqual(self.count('batches'), 2)
        self.assertEqual(self.conn.execute(
            'SELECT reason FROM fix1_shadow_test.quarantine'
        ).fetchall(), [('Existing prices do not point to one batch for the delivery day',)])

    def test_matching_prices_with_corrupt_evidence_are_quarantined_on_replay(self):
        self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'INSERT')
        corrupt = b'invalid archived evidence'
        self.conn.execute('UPDATE fix1_shadow_test.batches SET raw_html = %s', (corrupt,))
        before = self.conn.execute(
            'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        ).fetchall()

        for _ in range(2):
            self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'QUARANTINE')
        self.assertEqual(self.conn.execute(
            'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        ).fetchall(), before)
        self.assertEqual(self.conn.execute(
            'SELECT raw_html FROM fix1_shadow_test.batches'
        ).fetchall(), [(corrupt,)])
        self.assertEqual(self.conn.execute(
            'SELECT reason FROM fix1_shadow_test.quarantine'
        ).fetchall(), [('Existing price batch raw HTML hash mismatch',)])

    def test_replay_checks_prices_in_different_archived_html_with_valid_hash(self):
        from lxml import html

        manifest = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/manifest.json'
        (batch, raw), = load_batches(manifest)
        self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
        page = html.fromstring(raw)
        first_price, = page.xpath(
            '//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]/td[3]',
            period=f'{batch.delivery_date}_H01',
        )
        first_price.text = format(batch.rows[0][1] + Decimal('0.01'), 'f').replace('.', ',')
        changed_raw = html.tostring(page, encoding='utf-8')
        changed_hash = sha256(changed_raw).hexdigest()
        self.conn.execute(
            'UPDATE fix1_shadow_test.batches SET raw_html = %s, raw_sha256 = %s',
            (changed_raw, changed_hash),
        )
        for _ in range(2):
            self.assertEqual(archive_batch(self.conn, batch, raw), 'QUARANTINE')
        self.assertEqual(self.conn.execute(
            'SELECT period_start_utc, price FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        ).fetchall(), list(batch.rows))
        self.assertEqual(self.conn.execute(
            'SELECT raw_html FROM fix1_shadow_test.batches WHERE raw_sha256 = %s', (changed_hash,)
        ).fetchall(), [(changed_raw,)])
        self.assertEqual(self.conn.execute(
            'SELECT reason FROM fix1_shadow_test.quarantine'
        ).fetchall(), [('Existing price batch raw HTML disagrees with stored prices',)])

    def test_replay_requires_valid_archived_fix1_retrieval_time(self):
        manifest = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/manifest.json'
        (batch, raw), = load_batches(manifest)
        cutoff = cutoff_for_day(batch.delivery_date)
        prices_query = 'SELECT period_start_utc, price FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        for invalid in (cutoff - timedelta(seconds=1), cutoff, 'infinity'):
            with self.subTest(retrieved_at=invalid):
                with self.conn.transaction(force_rollback=True):
                    self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
                    self.assertEqual(archive_batch(self.conn, batch, raw), 'REPLAY')
                    self.conn.execute(
                        'UPDATE fix1_shadow_test.batches SET retrieved_at = %s', (invalid,),
                    )
                    for _ in range(2):
                        self.assertEqual(archive_batch(self.conn, batch, raw), 'QUARANTINE')
                    self.assertEqual(self.conn.execute(prices_query).fetchall(), list(batch.rows))
                    self.assertEqual(self.conn.execute(
                        'SELECT retrieved_at = %s, raw_html FROM fix1_shadow_test.batches', (invalid,),
                    ).fetchall(), [(True, raw)])
                    self.assertEqual(self.conn.execute(
                        'SELECT reason FROM fix1_shadow_test.quarantine',
                    ).fetchall(), [('Existing price batch has invalid Fix1 retrieval time',)])

    def test_replay_quarantines_out_of_day_periods_without_decoding_them(self):
        manifest = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/manifest.json'
        (batch, raw), = load_batches(manifest)
        first = batch.rows[0][0]
        query = ('SELECT period_start_utc::text, price, batch_id '
                 'FROM fix1_shadow_test.prices ORDER BY period_start_utc')
        self.conn.execute("SET TIME ZONE 'Europe/Warsaw'")
        self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
        for stamp in ('infinity', '-infinity', '10000-01-01 00:00:00+00', first - timedelta(hours=1)):
            with self.subTest(period_start_utc=stamp), self.conn.transaction(force_rollback=True):
                self.conn.execute(
                    'UPDATE fix1_shadow_test.prices SET period_start_utc = %s WHERE period_start_utc = %s',
                    (stamp, first),
                )
                before = self.conn.execute(query).fetchall()
                for _ in range(2):
                    self.assertEqual(archive_batch(self.conn, batch, raw), 'QUARANTINE')
                self.assertEqual(len(before), 24)
                self.assertEqual(self.conn.execute(query).fetchall(), before)
                self.assertEqual(self.count('batches'), 1)
                self.assertEqual(self.conn.execute(
                    'SELECT reason FROM fix1_shadow_test.quarantine'
                ).fetchall(), [('Existing delivery period is outside the Fix1 day',)])

        self.assertEqual(archive_batch(self.conn, batch, raw), 'REPLAY')

    def test_other_batch_quarantine_blocks_replay_of_matching_prices(self):
        manifest = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-13/manifest.json'
        (batch, raw), = load_batches(manifest)
        self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
        with self.conn.transaction():
            other_id, = self.conn.execute('''
                INSERT INTO fix1_shadow_test.batches
                    (delivery_date, raw_sha256, reference_sha256, parser_version,
                     source_url, retrieved_at, raw_html)
                SELECT delivery_date, raw_sha256, %s, parser_version,
                       source_url, retrieved_at, raw_html
                FROM fix1_shadow_test.batches RETURNING id
            ''', ('a' * 64,)).fetchone()
            self.conn.execute(
                'INSERT INTO fix1_shadow_test.quarantine(batch_id, reason) VALUES (%s, %s)',
                (other_id, 'Previously detected conflict'),
            )
        prices_query = 'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc'
        before = self.conn.execute(prices_query).fetchall()

        for _ in range(2):
            self.assertEqual(archive_batch(self.conn, batch, raw), 'QUARANTINE')
        self.assertEqual(len(before), 24)
        self.assertEqual(self.conn.execute(prices_query).fetchall(), before)
        self.assertEqual(self.count('batches'), 2)
        self.assertEqual(self.conn.execute(
            'SELECT batch_id, reason FROM fix1_shadow_test.quarantine'
        ).fetchall(), [(other_id, 'Previously detected conflict')])

    def test_mid_insert_failure_rolls_back_evidence_and_rows(self):
        import psycopg
        self.conn.execute('ALTER TABLE fix1_shadow_test.prices ADD CONSTRAINT force_test_failure CHECK(price<1000)')
        try:
            with self.assertRaises(psycopg.errors.CheckViolation): archive_batch(self.conn,self.batch,self.raw)
            self.assertEqual(self.count('prices'),0); self.assertEqual(self.count('batches'),0)
        finally:
            self.conn.execute('ALTER TABLE fix1_shadow_test.prices DROP CONSTRAINT force_test_failure')

    def test_stale_archive_transaction_is_rejected_then_retry_replays(self):
        with self.conn.transaction():
            self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            self.assertEqual(self.count('prices'), 0)

            with connect_test_database(DSN) as writer:
                self.assertEqual(archive_batch(writer, self.batch, self.raw), 'INSERT')

            with self.assertRaisesRegex(ValueError, 'requires READ COMMITTED'):
                archive_batch(self.conn, self.batch, self.raw)

        self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'REPLAY')
        self.assertEqual(self.count('prices'), 24)
        self.assertEqual(self.count('batches'), 1)
        self.assertEqual(self.count('quarantine'), 0)

    def test_day_lock_caps_transaction_timeout_without_changing_session_default(self):
        self.conn.execute("SET lock_timeout = '0'")
        with self.conn.transaction():
            self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'INSERT')
            self.assertEqual(self.conn.execute('SHOW lock_timeout').fetchone(), ('5s',))
        self.assertEqual(self.conn.execute('SHOW lock_timeout').fetchone(), ('0',))

    def test_concurrent_replays(self):
        def write(_):
            with connect_test_database(DSN) as conn: return archive_batch(conn,self.batch,self.raw)
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(write,range(4)))
        self.assertEqual(results.count('INSERT'),1); self.assertEqual(results.count('REPLAY'),3)
        self.assertEqual(self.count('prices'),24); self.assertEqual(self.count('batches'),1)

    def test_invalid_capture_timestamp_is_rejected_before_locking_existing_day(self):
        self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'INSERT')
        invalid = replace(self.batch, retrieved_at=datetime.fromisoformat('9999-12-31T23:59:59-01:00'))
        with patch('shadow_postgres_archive._lock_delivery_day') as lock:
            with self.assertRaisesRegex(ValueError, 'outside the supported UTC range'):
                archive_batch(self.conn, invalid, self.raw)
            lock.assert_not_called()
        self.assertEqual(self.count('prices'), 24)
        self.assertEqual(self.count('batches'), 1)
        self.assertEqual(self.count('quarantine'), 0)
        self.assertEqual(archive_batch(self.conn, self.batch, self.raw), 'REPLAY')

    def test_mismatched_raw_rejected_before_write(self):
        with self.assertRaises(ValueError): archive_batch(self.conn,self.batch,b'wrong')
        self.assertEqual(self.count('batches'),0)
