import os
from copy import deepcopy
from datetime import date,datetime,timezone
import unittest
from shadow_postgres_archive import connect_test_database
from shadow_readonly_audit import audit_connection,audit_pk5_utc_hours,connect_readonly
from shadow_scoring import expected_periods

DSN=os.environ.get('FIX1_SHADOW_TEST_DSN')

class PK5UtcHoursTests(unittest.TestCase):
    def test_complete_utc_days_include_both_dst_transitions(self):
        for day, count in ((date(2026, 3, 29), 23), (date(2026, 11, 2), 24),
                           (date(2026, 10, 25), 25)):
            with self.subTest(day=day):
                rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': '1000'}
                        for period in expected_periods(day)]
                self.assertEqual(len(rows), count)
                self.assertTrue(audit_pk5_utc_hours(day, rows)['complete_utc_hours'])
                duplicate = [*rows[:-1], deepcopy(rows[0])]
                result = audit_pk5_utc_hours(day, duplicate)
                self.assertFalse(result['complete_utc_hours'])
                self.assertEqual(result['duplicate_periods_utc'], [rows[0]['period_start_utc']])
                self.assertEqual(result['missing_periods_utc'], [rows[-1]['period_start_utc']])

    def test_legacy_and_naive_periods_do_not_pass_as_utc(self):
        result = audit_pk5_utc_hours(date(2026, 10, 25), [
            {'period': '02a', 'fcst_gen_unit_stor_prov': 'NaN'},
            {'period_start_utc': '2026-10-25T01:00:00', 'fcst_gen_unit_stor_prov': '1000'},
        ])
        self.assertFalse(result['complete_utc_hours'])
        self.assertEqual(result['rows_without_utc_period'], 1)
        self.assertEqual(result['rows_with_invalid_utc_period'], 1)
        self.assertEqual(result['valid_jwcd_rows'], 1)


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

    def test_audit_cutoff_uses_offset_on_issue_date(self):
        self.seed_dst()
        for delivery, started, cutoff in [
            (date(2026, 10, 25), datetime(2026, 10, 24, 7, 51, tzinfo=timezone.utc),
             '2026-10-24T07:50:00+00:00'),
            (date(2026, 11, 2), datetime(2026, 11, 1, 8, 51, tzinfo=timezone.utc),
             '2026-11-01T08:50:00+00:00'),
        ]:
            with self.subTest(delivery=delivery):
                self.conn.execute(
                    'UPDATE raw.snapshots SET business_date=%s, retrieved_at_utc=%s',
                    (delivery, started),
                )
                snapshot = audit_connection(self.conn)['snapshots'][0]
                self.assertEqual(snapshot['cutoff_utc'], cutoff)
                self.assertEqual(snapshot['availability_status'], 'RETRIEVAL_STARTED_AFTER_CUTOFF')
                self.assertFalse(snapshot['model_ready'])

    def test_audit_distinguishes_late_and_unverified_publications(self):
        from psycopg.types.json import Jsonb

        self.seed_dst()
        self.conn.execute(
            'UPDATE raw.snapshots SET payload=%s',
            (Jsonb({'pk5l-wp': [
                {'publication_ts': '2026-10-24T09:50:00+02:00'},
                {'publication_ts': '2026-10-24T07:51:00Z'},
                {'publication_ts': '2026-10-24T08:00:00'},
                {'publication_ts': 'invalid'},
            ]}),),
        )

        snapshot = audit_connection(self.conn)['snapshots'][0]
        self.assertEqual(snapshot['publication_after_cutoff'], 1)
        self.assertEqual(snapshot['publication_unverified'], 2)
        self.assertEqual(snapshot['availability_status'], 'PUBLICATION_AFTER_CUTOFF')
        self.assertFalse(snapshot['model_ready'])

    def test_missing_tables_reported_without_creation(self):
        self.conn.execute('DROP SCHEMA raw CASCADE');self.conn.execute('DROP SCHEMA actual CASCADE')
        out=audit_connection(self.conn)
        self.assertFalse(out['tables']['raw.snapshots']);self.assertEqual(out['snapshots'],[])
        self.assertIsNone(self.conn.execute("SELECT to_regclass('raw.snapshots')").fetchone()[0])
        self.conn.execute('CREATE SCHEMA raw');self.conn.execute('CREATE SCHEMA actual')

    def test_v3_archive_time_identifies_candidates_without_admitting_history(self):
        from psycopg.types.json import Jsonb

        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN created_at timestamptz')
        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN source_version text')
        day = date(2026, 10, 25)
        retrieved = datetime.fromisoformat('2026-10-24T07:30:00+00:00')
        cutoff = '2026-10-24T07:50:00+00:00'
        rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': 1000,
                 'publication_ts': '2026-10-24T07:20:00Z'} for period in expected_periods(day)]
        for archive_time, source, publications, eligible in (
            (cutoff, 'energy-collector-v3', rows, True),
            ('2026-10-24T07:50:01+00:00', 'energy-collector-v3', rows, False),
            ('2026-10-24T07:29:00+00:00', 'energy-collector-v3', rows, False),
            (cutoff, 'imported', rows, False),
            (cutoff, 'energy-collector-v3', [*rows[:-1], {'period': '25'}], False),
        ):
            with self.subTest(archive_time=archive_time, source=source, eligible=eligible):
                self.conn.execute('DELETE FROM raw.snapshots')
                self.conn.execute(
                    'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s,%s,%s)',
                    (1, day, retrieved, Jsonb({'pk5l-wp': publications}), archive_time, source),
                )
                result = audit_connection(self.conn)
                snapshot, = result['snapshots']
                self.assertEqual(snapshot['archive_timing_candidate'], eligible)
                self.assertEqual(result['archive_timing_candidate_days'], [day.isoformat()] if eligible else [])
                self.assertEqual(result['archive_hourly_candidate_days'], [day.isoformat()] if eligible else [])
                self.assertFalse(snapshot['model_ready'])

        self.conn.execute('UPDATE raw.snapshots SET payload=%s', (Jsonb({'pk5l-wp': [
            {key: value for key, value in row.items() if key != 'period_start_utc'}
            for row in rows
        ]}),))
        result = audit_connection(self.conn)
        self.assertEqual(result['archive_timing_candidate_days'], [day.isoformat()])
        self.assertEqual(result['archive_hourly_candidate_days'], [])

    def test_hourly_candidate_selection_flags_conflicting_latest_versions(self):
        from psycopg.types.json import Jsonb

        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN created_at timestamptz')
        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN source_version text')
        day = date(2026, 11, 2)
        periods = expected_periods(day)
        retrieved = datetime.fromisoformat('2026-11-01T08:30:00+00:00')

        def insert(identifier, minute, jwcd, reverse=False):
            rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': jwcd,
                     'publication_ts': '2026-11-01T08:00:00Z'} for period in periods]
            if reverse:
                rows.reverse()
            self.conn.execute(
                'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s,%s,%s)',
                (identifier, day, retrieved, Jsonb({'pk5l-wp': rows}),
                 retrieved.replace(minute=minute), 'energy-collector-v3'),
            )

        def selection():
            return audit_connection(self.conn)['archive_hourly_candidate_selection'][day.isoformat()]

        insert(1, 40, 999)
        insert(2, 50, 1000)
        insert(3, 51, 2000)
        self.assertEqual(selection()['provisional_snapshot_id'], '2')
        insert(4, 50, '1000.00', reverse=True)
        self.assertEqual(selection()['provisional_snapshot_id'], '4')
        insert(5, 50, 1001)
        result = selection()
        self.assertIsNone(result['provisional_snapshot_id'])
        self.assertEqual(result['rejected_snapshots'], tuple(
            (str(identifier), 'CONFLICTING_LATEST_SNAPSHOTS') for identifier in (2, 4, 5)
        ))
