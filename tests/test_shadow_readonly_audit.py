import os
from datetime import date,datetime,timezone
import unittest
from shadow_postgres_archive import connect_test_database
from shadow_readonly_audit import audit_connection,connect_readonly

DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

@unittest.skipUnless(DSN,'Set explicit disposable PostgreSQL DSN')
class ReadonlyAuditTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect_test_database(DSN)
        # Mimic existing legacy schemas ONLY in the disposable local CI database.
        self.conn.execute('CREATE SCHEMA raw');self.conn.execute('CREATE SCHEMA actual')
        self.conn.execute('CREATE TABLE raw.snapshots(id bigint,business_date date,retrieved_at_utc timestamptz,payload jsonb)')
        self.conn.execute('CREATE TABLE actual.tge_fix1(delivery_date date,hour int,price_pln_mwh numeric)')
    def tearDown(self):
        self.conn.execute('DROP SCHEMA raw CASCADE');self.conn.execute('DROP SCHEMA actual CASCADE');self.conn.close()

    def seed_dst(self):
        from psycopg.types.json import Jsonb
        rows=[dict(period=str(min(h,24)),fcst_gen_unit_stor_prov=10000,publication_ts='2026-10-24 08:00:00') for h in range(1,26)]
        self.conn.execute('INSERT INTO raw.snapshots VALUES(%s,%s,%s,%s)',
            (1,date(2026,10,25),datetime(2026,10,24,7,tzinfo=timezone.utc),Jsonb({'pk5l-wp':rows})))
        with self.conn.cursor() as c:c.executemany('INSERT INTO actual.tge_fix1 VALUES(%s,%s,%s)',[(date(2026,10,25),h,60) for h in range(1,25)])

    def test_dst_incompleteness_and_unproven_availability_are_explicit(self):
        self.seed_dst();out=audit_connection(self.conn)
        self.assertTrue(out['transaction_read_only'])
        s=out['snapshots'][0];self.assertEqual((s['rows'],s['expected_periods'],s['repeated_raw_labels']),(25,25,1))
        self.assertFalse(s['model_ready']);self.assertIn('UNPROVEN',s['availability_status'])
        a=out['actual_days'][0];self.assertFalse(a['structurally_complete']);self.assertEqual(a['prices_equal_60_or_2026'],24)

    def test_audit_does_not_mutate_existing_records(self):
        self.seed_dst();before=self.conn.execute('SELECT * FROM actual.tge_fix1 ORDER BY hour').fetchall()
        audit_connection(self.conn)
        self.assertEqual(before,self.conn.execute('SELECT * FROM actual.tge_fix1 ORDER BY hour').fetchall())

    def test_connection_defaults_reject_writes_at_database_level(self):
        import psycopg
        with connect_readonly(DSN) as conn:
            self.assertEqual(conn.execute('SHOW default_transaction_read_only').fetchone()[0],'on')
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute('INSERT INTO actual.tge_fix1 VALUES(%s,%s,%s)',(date(2026,11,2),1,100))
            self.assertEqual(audit_connection(conn)['actual_days'],[])

    def test_audit_uses_same_snapshot_across_queries(self):
        from unittest.mock import patch

        self.seed_dst()
        execute = self.conn.execute
        with connect_test_database(DSN) as writer:
            def execute_with_concurrent_insert(query, *args, **kwargs):
                if 'FROM actual.tge_fix1 GROUP BY' in query:
                    writer.execute(
                        'INSERT INTO actual.tge_fix1 VALUES (%s,%s,%s)',
                        (date(2026, 10, 25), 25, 100),
                    )
                return execute(query, *args, **kwargs)

            with patch.object(self.conn, 'execute', side_effect=execute_with_concurrent_insert):
                report = audit_connection(self.conn)

        self.assertEqual(report['actual_days'][0]['rows'], 24)
        self.assertEqual(execute('SELECT count(*) FROM actual.tge_fix1').fetchone()[0], 25)

    def test_distinct_hours_can_still_be_incomplete(self):
        with self.conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO actual.tge_fix1 VALUES (%s,%s,%s)',
                [(date(2026, 11, 2), hour, 100) for hour in (*range(1, 24), 25)],
            )

        day = audit_connection(self.conn)['actual_days'][0]
        self.assertEqual(day['distinct_legacy_hours'], 24)
        self.assertFalse(day['structurally_complete'])
        self.assertEqual(day['missing_legacy_hours'], [24])
        self.assertEqual(day['unexpected_legacy_hours'], [25])

    def test_missing_tables_reported_without_creation(self):
        self.conn.execute('DROP SCHEMA raw CASCADE');self.conn.execute('DROP SCHEMA actual CASCADE')
        out=audit_connection(self.conn)
        self.assertFalse(out['tables']['raw.snapshots']);self.assertEqual(out['snapshots'],[])
        self.assertIsNone(self.conn.execute("SELECT to_regclass('raw.snapshots')").fetchone()[0])
        self.conn.execute('CREATE SCHEMA raw');self.conn.execute('CREATE SCHEMA actual')
