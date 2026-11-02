import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import test_shadow_archive_batch as fixtures
from shadow_archive_batch import prepare_batch
from shadow_postgres_archive import connect_test_database, initialize, archive_batch
from shadow_postgres_scores import initialize_scores, score_archived_day

DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

@unittest.skipUnless(DSN,'Set FIX1_SHADOW_TEST_DSN for PostgreSQL integration')
class ScorePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN); initialize(self.conn); initialize_scores(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.scores,fix1_shadow_test.quarantine,fix1_shadow_test.prices,fix1_shadow_test.batches RESTART IDENTITY')
        kw=fixtures.ArchiveAdmissionTests().args(); self.batch=prepare_batch(**kw)
        archive_batch(self.conn,self.batch,kw['raw'])
        self.kw=dict(day=self.batch.delivery_date,forecast=list(self.batch.rows),
            forecast_id='synthetic-equals-actual',model_version='synthetic-test-only',forecast_status='SHADOW',
            issued_at=datetime(2026,11,1,8,tzinfo=timezone.utc),
            publication_ts=datetime(2026,11,1,6,tzinfo=timezone.utc),
            retrieved_at=datetime(2026,11,1,7,tzinfo=timezone.utc))

    def tearDown(self): self.conn.close()
    def count(self): return self.conn.execute('SELECT count(*) FROM fix1_shadow_test.scores').fetchone()[0]

    def test_store_replay_zero_metrics_and_coverage(self):
        decision,s=score_archived_day(self.conn,**self.kw)
        self.assertEqual(decision,'INSERT'); self.assertEqual(s.mae,0)
        self.assertEqual(s.mape_periods,24); self.assertEqual(s.bias,0)
        self.kw['forecast']=list(reversed(self.kw['forecast']))
        self.assertEqual(score_archived_day(self.conn,**self.kw)[0],'REPLAY')
        self.assertEqual(self.count(),1)

    def test_changed_forecast_same_identity_never_overwrites(self):
        score_archived_day(self.conn,**self.kw)
        old=self.conn.execute('SELECT payload FROM fix1_shadow_test.scores').fetchone()
        p,v=self.kw['forecast'][0]; self.kw['forecast'][0]=(p,v+10)
        with self.assertRaisesRegex(ValueError,'identity conflict'): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.conn.execute('SELECT payload FROM fix1_shadow_test.scores').fetchone(),old)
        self.assertEqual(self.count(),1)

    def test_wrong_actual_or_missing_period_blocks_scoring(self):
        p,_=self.batch.rows[0]
        self.conn.execute('UPDATE fix1_shadow_test.prices SET price=60 WHERE period_start_utc=%s',(p,))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)
        self.conn.execute('DELETE FROM fix1_shadow_test.prices WHERE period_start_utc=%s',(p,))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)

    def test_quarantined_batch_blocks_scoring(self):
        bid=self.conn.execute('SELECT id FROM fix1_shadow_test.batches').fetchone()[0]
        self.conn.execute('INSERT INTO fix1_shadow_test.quarantine(batch_id,reason) VALUES (%s,%s)',(bid,'test'))
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)

    def test_late_forecast_has_no_stored_score(self):
        self.kw['issued_at']=datetime(2026,11,1,8,51,tzinfo=timezone.utc)
        with self.assertRaises(ValueError): score_archived_day(self.conn,**self.kw)
        self.assertEqual(self.count(),0)

    def test_four_concurrent_scores_only_insert_once(self):
        def write(_):
            with connect_test_database(DSN) as conn: return score_archived_day(conn,**self.kw)[0]
        with ThreadPoolExecutor(max_workers=4) as pool: decisions=list(pool.map(write,range(4)))
        self.assertEqual(decisions.count('INSERT'),1); self.assertEqual(decisions.count('REPLAY'),3)
        self.assertEqual(self.count(),1)
