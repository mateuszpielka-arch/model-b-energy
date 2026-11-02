"""Integration tests use only an explicitly supplied local disposable database."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from shadow_postgres_archive import connect_test_database, initialize, archive_batch
import test_shadow_archive_batch as fixtures
from shadow_archive_batch import prepare_batch

DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')

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

    def test_mid_insert_failure_rolls_back_evidence_and_rows(self):
        import psycopg
        self.conn.execute('ALTER TABLE fix1_shadow_test.prices ADD CONSTRAINT force_test_failure CHECK(price<1000)')
        try:
            with self.assertRaises(psycopg.errors.CheckViolation): archive_batch(self.conn,self.batch,self.raw)
            self.assertEqual(self.count('prices'),0); self.assertEqual(self.count('batches'),0)
        finally:
            self.conn.execute('ALTER TABLE fix1_shadow_test.prices DROP CONSTRAINT force_test_failure')

    def test_concurrent_replays(self):
        def write(_):
            with connect_test_database(DSN) as conn: return archive_batch(conn,self.batch,self.raw)
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(write,range(4)))
        self.assertEqual(results.count('INSERT'),1); self.assertEqual(results.count('REPLAY'),3)
        self.assertEqual(self.count('prices'),24); self.assertEqual(self.count('batches'),1)

    def test_mismatched_raw_rejected_before_write(self):
        with self.assertRaises(ValueError): archive_batch(self.conn,self.batch,b'wrong')
        self.assertEqual(self.count('batches'),0)
