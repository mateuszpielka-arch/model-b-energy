from dataclasses import replace
from datetime import datetime,timedelta
from decimal import Decimal
import os
import unittest
import test_shadow_archive_batch as fixtures
from shadow_archive_batch import prepare_batch,revalidate_batch
from shadow_postgres_archive import connect_test_database,initialize,archive_batch

DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

class RevalidationTests(unittest.TestCase):
    def batch(self):
        kw=fixtures.ArchiveAdmissionTests().args();return prepare_batch(**kw),kw['raw']

    def test_unchanged_batch_and_reordered_rows_are_canonical(self):
        b,raw=self.batch()
        self.assertEqual(revalidate_batch(b,raw),b)
        self.assertEqual(revalidate_batch(replace(b,rows=tuple(reversed(b.rows))),raw),b)

    def test_modified_price_with_valid_raw_hash_is_rejected(self):
        b,raw=self.batch();rows=list(b.rows);p,v=rows[0];rows[0]=(p,v+1)
        with self.assertRaisesRegex(ValueError,'reference mismatch'):
            revalidate_batch(replace(b,rows=tuple(rows)),raw)

    def test_unsupported_version_and_wrong_source_rejected(self):
        b,raw=self.batch()
        for bad in (replace(b,parser_version='unknown'),replace(b,source_url='https://example.com')):
            with self.subTest(bad=bad.parser_version),self.assertRaises(ValueError):revalidate_batch(bad,raw)

    def test_naive_retrieval_and_invalid_reference_hash_rejected(self):
        b,raw=self.batch()
        for bad in (replace(b,retrieved_at=datetime(2026,11,1)),replace(b,reference_sha256='bad')):
            with self.assertRaises(ValueError):revalidate_batch(bad,raw)

    def test_duplicate_or_wrong_utc_period_rejected(self):
        b,raw=self.batch();rows=list(b.rows);p,v=rows[0];rows[0]=(p-timedelta(days=1),v)
        for bad in (replace(b,rows=tuple(rows)),replace(b,rows=b.rows+b.rows[:1])):
            with self.assertRaises(ValueError):revalidate_batch(bad,raw)

    def test_nonfinite_price_and_changed_raw_rejected(self):
        b,raw=self.batch();rows=list(b.rows);p,v=rows[0];rows[0]=(p,Decimal('NaN'))
        with self.assertRaises(ValueError):revalidate_batch(replace(b,rows=tuple(rows)),raw)
        with self.assertRaises(ValueError):revalidate_batch(b,raw+b'changed')

@unittest.skipUnless(DSN,'Set isolated PostgreSQL DSN')
class WriterBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN);initialize(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.quarantine,fix1_shadow_test.prices,fix1_shadow_test.batches RESTART IDENTITY CASCADE')
        kw=fixtures.ArchiveAdmissionTests().args();self.batch=prepare_batch(**kw);self.raw=kw['raw']
    def tearDown(self):self.conn.close()
    def forged(self):
        rows=list(self.batch.rows);p,v=rows[0];rows[0]=(p,v+100)
        return replace(self.batch,rows=tuple(rows))

    def test_forged_complete_batch_writes_nothing(self):
        with self.assertRaises(ValueError):archive_batch(self.conn,self.forged(),self.raw)
        for table in ('batches','prices','quarantine'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.'+table).fetchone()[0],0)

    def test_forged_replay_does_not_touch_good_archive(self):
        archive_batch(self.conn,self.batch,self.raw)
        before=self.conn.execute('SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc').fetchall()
        with self.assertRaises(ValueError):archive_batch(self.conn,self.forged(),self.raw)
        self.assertEqual(before,self.conn.execute('SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc').fetchall())
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone()[0],0)
