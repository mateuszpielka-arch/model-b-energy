import os
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
import unittest
from shadow_postgres_archive import connect_test_database
from shadow_asof import cutoff_for_day
from shadow_readonly_audit import (
    audit_connection, audit_pk5_utc_hours, connect_readonly, provisional_comparison_coverage,
)
from shadow_scoring import WARSAW, expected_periods

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


class ProvisionalCoverageTests(unittest.TestCase):
    def test_calendar_windows_use_target_cutoff_and_never_fill_missing_days(self):
        first = date(2026, 8, 1)
        days = [first + timedelta(days=i) for i in range(91)]
        actuals = [dict(
            day=day.isoformat(), single_recorded_source=True,
            expected_periods=len(expected_periods(day)), period_basis='EXPLICIT_UTC',
            next_delivery_training_timing='TIMING_CANDIDATE',
            latest_retrieved_at_utc=cutoff_for_day(day + timedelta(days=1)).isoformat(),
        ) for day in days]
        selections = {day.isoformat(): {'provisional_snapshot_id': str(i + 1)}
                      for i, day in enumerate(days)}
        result = provisional_comparison_coverage(actuals, selections)
        self.assertEqual(result['paired_days'], 91)
        self.assertEqual(result['longest_paired_run'], 91)
        for window in (30, 60, 90):
            self.assertEqual(result['comparison_candidates'][f'{window}D'],
                             [day.isoformat() for day in days[window:]])

        result = provisional_comparison_coverage(
            actuals, selections, excluded_days={days[45].isoformat()},
        )
        self.assertEqual(result['paired_days'], 90)
        self.assertEqual(result['longest_paired_run'], 45)
        self.assertEqual(result['comparison_candidates']['60D'], [])
        self.assertEqual(result['comparison_candidates']['90D'], [])

        matched = {day.isoformat() for day in days}
        matched.remove(days[45].isoformat())
        result = provisional_comparison_coverage(actuals, selections, included_days=matched)
        self.assertEqual(result['paired_days'], 90)
        self.assertEqual(result['longest_paired_run'], 45)
        self.assertEqual(result['comparison_candidates']['90D'], [])
        self.assertIn(days[-1].isoformat(), result['comparison_candidates']['30D'])
        matched.add(days[45].isoformat())
        result = provisional_comparison_coverage(actuals, selections, included_days=matched)
        self.assertEqual(result['comparison_candidates']['90D'], [days[-1].isoformat()])

        actuals[-2]['latest_retrieved_at_utc'] = (
            cutoff_for_day(days[-1]) + timedelta(seconds=1)
        ).isoformat()
        actuals[-2]['next_delivery_training_timing'] = 'AFTER_NEXT_ISSUE_CUTOFF'
        result = provisional_comparison_coverage(actuals, selections)
        self.assertEqual(result['longest_paired_run'], 91)
        self.assertEqual(result['comparison_candidates']['90D'], [])
        self.assertEqual(result['comparison_blocked_by_late_fix1']['90D'], {
            days[-1].isoformat(): [days[-2].isoformat()],
        })

        selections[days[45].isoformat()]['provisional_snapshot_id'] = None
        result = provisional_comparison_coverage(actuals, selections)
        self.assertEqual(result['longest_paired_run'], 45)
        self.assertEqual(result['comparison_candidates']['60D'], [])
        self.assertEqual(result['comparison_blocked_by_late_fix1']['90D'], {})


    def test_dst_legacy_hours_are_excluded_from_provisional_coverage(self):
        for day, count in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25),
                           (date(2026, 11, 2), 24)):
            with self.subTest(day=day):
                actual = dict(
                    day=day.isoformat(), single_recorded_source=True,
                    expected_periods=count, period_basis='LEGACY_HOUR',
                    next_delivery_training_timing='TIMING_CANDIDATE',
                    latest_retrieved_at_utc=cutoff_for_day(day + timedelta(days=1)).isoformat(),
                )
                selections = {day.isoformat(): {'provisional_snapshot_id': '1'}}
                result = provisional_comparison_coverage([actual], selections)
                self.assertEqual(result['paired_days'], int(count == 24))

                actual['period_basis'] = 'EXPLICIT_UTC'
                result = provisional_comparison_coverage([actual], selections)
                self.assertEqual(result['paired_days'], 1)


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
        self.assertEqual(out['provisional_comparison_coverage']['paired_days'], 0)
        self.assertEqual(out['delivery_coverage'], [{
            'day': '2026-10-25', 'pk5_status': 'NO_HOURLY_CANDIDATE', 'fix1_status': 'INCOMPLETE',
        }])
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
                if 'FROM actual.tge_fix1 WHERE' in query:
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

    def test_missing_legacy_retrieval_evidence_does_not_abort_audit(self):
        from psycopg.types.json import Jsonb

        self.seed_dst()
        day = date(2026, 10, 25)
        rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': 1000,
                 'publication_ts': '2026-10-24T06:50:00Z'} for period in expected_periods(day)]
        self.conn.execute(
            'ALTER TABLE raw.snapshots ADD COLUMN created_at timestamptz, ADD COLUMN source_version text'
        )
        self.conn.execute(
            'UPDATE raw.snapshots SET payload=%s, created_at=%s, source_version=%s',
            (Jsonb({'pk5l-wp': rows}), '2026-10-24T07:45:00Z', 'energy-collector-v3'),
        )
        self.assertTrue(audit_connection(self.conn)['snapshots'][0]['archive_hourly_candidate'])
        self.conn.execute('UPDATE raw.snapshots SET retrieved_at_utc = NULL')
        for missing_column in (False, True):
            with self.subTest(missing_column=missing_column):
                if missing_column:
                    self.conn.execute('ALTER TABLE raw.snapshots DROP COLUMN retrieved_at_utc')
                report = audit_connection(self.conn)
                snapshot, = report['snapshots']
                self.assertIsNone(snapshot['retrieved_at_utc'])
                self.assertEqual(snapshot['availability_status'], 'UNPROVEN_NO_VALID_RETRIEVAL_START')
                self.assertFalse(snapshot['archive_timing_candidate'])
                self.assertEqual(report['archive_hourly_candidate_selection'], {})
                self.assertEqual(report['actual_days'][0]['day'], '2026-10-25')

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
            publication = '2026-11-01T07:00:00Z' if identifier == 1 else '2026-11-01T08:00:00Z'
            rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': jwcd,
                     'publication_ts': publication} for period in periods]
            if reverse:
                rows.reverse()
            self.conn.execute(
                'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s,%s,%s)',
                (identifier, day, retrieved, Jsonb({'pk5l-wp': rows}),
                 retrieved.replace(minute=minute), 'energy-collector-v3'),
            )

        def selection():
            return audit_connection(self.conn)['archive_hourly_candidate_selection'][day.isoformat()]

        insert(1, 50, 999)
        insert(2, 49, 1000)
        insert(3, 51, 2000)
        self.assertEqual(selection()['provisional_snapshot_id'], '2')
        insert(4, 49, '1000.00', reverse=True)
        self.assertEqual(selection()['provisional_snapshot_id'], '4')
        insert(5, 49, 1001)
        result = selection()
        self.assertIsNone(result['provisional_snapshot_id'])
        self.assertEqual(result['rejected_snapshots'], (('1', 'SUPERSEDED_JWCD_PUBLICATIONS'),) + tuple(
            (str(identifier), 'CONFLICTING_LATEST_SNAPSHOTS') for identifier in (2, 4, 5)
        ))

        self.conn.execute('DELETE FROM raw.snapshots WHERE id=5')
        insert(6, 45, 1001)
        result = selection()
        self.assertIsNone(result['provisional_snapshot_id'])
        self.assertEqual(result['rejected_snapshots'], (('1', 'SUPERSEDED_JWCD_PUBLICATIONS'),) + tuple(
            (str(identifier), 'CONFLICTING_JWCD_PUBLICATION') for identifier in (2, 4, 6)
        ))

    def test_audit_bounds_both_tables_by_inclusive_delivery_dates(self):
        from psycopg.types.json import Jsonb

        self.seed_dst()
        first, second, third = (date(2026, 10, day) for day in (25, 26, 27))
        for day in (second, third):
            self.conn.execute(
                'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s)',
                (day.day, day, datetime(2026, 10, 24, 7, tzinfo=timezone.utc),
                 Jsonb({'pk5l-wp': []})),
            )
            self.conn.execute('INSERT INTO actual.tge_fix1 VALUES (%s,1,100)', (day,))

        for kwargs, days in (
            ({'start_day': second, 'end_day': second}, [second]),
            ({'start_day': second}, [second, third]),
            ({'end_day': second}, [first, second]),
            ({}, [first, second, third]),
        ):
            with self.subTest(kwargs=kwargs):
                report = audit_connection(self.conn, **kwargs)
                expected = [day.isoformat() for day in days]
                self.assertEqual([row['day'] for row in report['snapshots']], expected)
                self.assertEqual([row['day'] for row in report['actual_days']], expected)
                for key in ('start_day', 'end_day'):
                    self.assertEqual(report[key], kwargs[key].isoformat() if key in kwargs else None)

        with self.assertRaisesRegex(ValueError, 'Start day must not be after'):
            audit_connection(self.conn, start_day=third, end_day=first)

    def test_delivery_coverage_includes_dates_present_in_only_one_source(self):
        from psycopg.types.json import Jsonb

        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN created_at timestamptz')
        self.conn.execute('ALTER TABLE raw.snapshots ADD COLUMN source_version text')
        pk5_day, fix1_day = date(2026, 11, 2), date(2026, 11, 3)
        rows = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': 1000,
                 'publication_ts': '2026-11-01T08:00:00Z'}
                for period in expected_periods(pk5_day)]
        retrieved = datetime.fromisoformat('2026-11-01T08:30:00+00:00')
        with self.conn.cursor() as cursor:
            cursor.executemany('INSERT INTO actual.tge_fix1 VALUES (%s,%s,100)',
                               [(fix1_day, hour) for hour in range(1, 25)])
        for identifier, jwcd_status in ((1, 'PROVISIONAL'), (2, 'CANDIDATES_REJECTED')):
            rows[0]['fcst_gen_unit_stor_prov'] += 1
            self.conn.execute(
                'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s,%s,%s)',
                (identifier, pk5_day, retrieved, Jsonb({'pk5l-wp': rows}),
                 retrieved, 'energy-collector-v3'),
            )
            self.assertEqual(audit_connection(self.conn)['delivery_coverage'], [
                {'day': pk5_day.isoformat(), 'pk5_status': jwcd_status, 'fix1_status': 'MISSING'},
                {'day': fix1_day.isoformat(), 'pk5_status': 'MISSING',
                 'fix1_status': 'COMPLETE_UNVERIFIED'},
            ])

    def test_missing_delivery_ranges_cover_empty_and_partially_observed_windows(self):
        start = date(2026, 10, 24)
        end = date(2026, 10, 29)

        report = audit_connection(self.conn, start_day=start, end_day=end)
        self.assertEqual(report['delivery_coverage'], [])
        self.assertEqual(report['missing_delivery_ranges'], [
            {'start_day': '2026-10-24', 'end_day': '2026-10-29', 'days': 6},
        ])
        self.assertEqual(audit_connection(self.conn, start_day=start)['missing_delivery_ranges'], [])

        self.seed_dst()
        self.conn.execute('INSERT INTO actual.tge_fix1 VALUES (%s,1,100)', (date(2026, 10, 28),))
        report = audit_connection(self.conn, start_day=start, end_day=end)
        internal_gap = {'start_day': '2026-10-26', 'end_day': '2026-10-27', 'days': 2}
        self.assertEqual(report['missing_delivery_ranges'], [
            {'start_day': '2026-10-24', 'end_day': '2026-10-24', 'days': 1},
            internal_gap,
            {'start_day': '2026-10-29', 'end_day': '2026-10-29', 'days': 1},
        ])
        self.assertEqual(audit_connection(self.conn)['missing_delivery_ranges'], [internal_gap])

    def test_complete_hours_with_missing_or_nonfinite_prices_are_flagged(self):
        from decimal import Decimal

        day = date(2026, 11, 2)
        with self.conn.cursor() as cursor:
            prices = [None, Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity'),
                      Decimal('-12.34'), Decimal('0'), *([Decimal('100')] * 18)]
            cursor.executemany(
                'INSERT INTO actual.tge_fix1 VALUES (%s,%s,%s)',
                [(day, hour, price) for hour, price in enumerate(prices, 1)],
            )

        report = audit_connection(self.conn)
        actual, = report['actual_days']
        self.assertTrue(actual['structurally_complete'])
        self.assertFalse(actual['complete_finite_prices'])
        self.assertEqual(actual['missing_price_rows'], 1)
        self.assertEqual(actual['nonfinite_price_rows'], 3)
        self.assertEqual(report['delivery_coverage'][0]['fix1_status'], 'INVALID_PRICES')

        self.conn.execute('UPDATE actual.tge_fix1 SET price_pln_mwh=100 WHERE hour <= 4')
        report = audit_connection(self.conn)
        self.assertTrue(report['actual_days'][0]['complete_finite_prices'])
        self.assertEqual(report['delivery_coverage'][0]['fix1_status'], 'COMPLETE_UNVERIFIED')

    def test_fix1_audit_distinguishes_first_seen_from_current_retrieval(self):
        day = date(2026, 11, 2)
        self.conn.execute('INSERT INTO actual.tge_fix1 VALUES (%s,1,100)', (day,))
        legacy, = audit_connection(self.conn)['actual_days']
        self.assertIsNone(legacy['latest_retrieved_at_utc'])
        self.assertEqual(legacy['rows_missing_price_timing'], 1)

        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN first_seen_at_utc timestamptz')
        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN retrieved_at_utc timestamptz')
        with self.conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO actual.tge_fix1 VALUES (%s,%s,100,%s,%s)',
                [(day, 2, '2026-11-01T10:05:00+01:00', '2026-11-02T06:00:00Z'),
                 (day, 3, '2026-11-01T10:05:00+01:00', '2026-11-01T08:00:00Z'),
                 (day, 4, '2026-11-01T10:05:00+01:00', '2026-11-01T09:05:00Z')],
            )

        actual, = audit_connection(self.conn)['actual_days']
        self.assertEqual(actual['latest_first_seen_at_utc'], '2026-11-01T09:05:00+00:00')
        self.assertEqual(actual['latest_retrieved_at_utc'], '2026-11-02T06:00:00+00:00')
        self.assertEqual(actual['rows_missing_price_timing'], 1)
        self.assertEqual(actual['rows_with_invalid_price_timing'], 1)
        self.assertEqual(actual['rows_retrieved_after_first_seen'], 1)
        self.assertEqual(actual['quality_status'], 'UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION')

    def test_fix1_audit_does_not_infer_timezone_for_legacy_timestamps(self):
        day = date(2026, 11, 2)
        self.conn.execute(
            'ALTER TABLE actual.tge_fix1 ADD COLUMN first_seen_at_utc timestamptz, '
            'ADD COLUMN retrieved_at_utc timestamptz'
        )
        self.conn.execute(
            "INSERT INTO actual.tge_fix1 SELECT %s, hour, 100, '2026-11-01T09:05:00Z', "
            "'2026-11-01T09:05:00Z' FROM generate_series(1, 24) AS hours(hour)", (day,),
        )
        baseline, = audit_connection(self.conn)['actual_days']
        self.assertEqual(baseline['next_delivery_training_timing'], 'TIMING_CANDIDATE')

        for column in ('retrieved_at_utc', 'first_seen_at_utc'):
            with self.subTest(column=column):
                self.conn.execute(
                    f'ALTER TABLE actual.tge_fix1 ALTER COLUMN {column} TYPE timestamp '
                    f"USING {column} AT TIME ZONE 'UTC'"
                )
                actual, = audit_connection(self.conn)['actual_days']
                self.assertIsNone(actual[f'latest_{column}'])
                self.assertTrue(actual['complete_finite_prices'])
                self.assertEqual(actual['next_delivery_training_timing'], 'UNPROVEN')
                self.assertEqual(actual['rows_missing_price_timing'], 24)
                self.conn.execute(
                    f'ALTER TABLE actual.tge_fix1 ALTER COLUMN {column} TYPE timestamptz '
                    f"USING {column} AT TIME ZONE 'UTC'"
                )

    def test_fix1_audit_treats_infinite_timestamps_as_unavailable(self):
        day = date(2026, 11, 2)
        self.conn.execute(
            'ALTER TABLE actual.tge_fix1 ADD COLUMN first_seen_at_utc timestamptz, '
            'ADD COLUMN retrieved_at_utc timestamptz'
        )
        self.conn.execute(
            "INSERT INTO actual.tge_fix1 SELECT %s, hour, 100, '2026-11-01T09:05:00Z', "
            "'2026-11-01T09:05:00Z' FROM generate_series(1, 24) AS hours(hour)", (day,),
        )
        self.conn.execute(
            "UPDATE actual.tge_fix1 SET retrieved_at_utc='infinity' WHERE hour=1"
        )
        self.conn.execute(
            "UPDATE actual.tge_fix1 SET first_seen_at_utc='-infinity' WHERE hour=2"
        )
        actual, = audit_connection(self.conn)['actual_days']
        self.assertTrue(actual['complete_finite_prices'])
        self.assertEqual(actual['next_delivery_training_timing'], 'UNPROVEN')
        self.assertEqual(actual['rows_missing_price_timing'], 2)
        self.assertEqual(actual['rows_with_invalid_price_timing'], 0)
        self.assertEqual(actual['rows_retrieved_after_first_seen'], 0)
        self.assertEqual(actual['latest_first_seen_at_utc'], '2026-11-01T09:05:00+00:00')
        self.assertEqual(actual['latest_retrieved_at_utc'], '2026-11-01T09:05:00+00:00')

    def test_fix1_audit_reports_single_mixed_and_missing_recorded_sources(self):
        day = date(2026, 11, 2)
        with self.conn.cursor() as cursor:
            cursor.executemany('INSERT INTO actual.tge_fix1 VALUES (%s,%s,100)',
                               [(day, hour) for hour in range(1, 25)])

        def report():
            return audit_connection(self.conn)['actual_days'][0]

        actual = report()
        self.assertEqual(actual['rows_without_usable_source_metadata'], 24)
        self.assertFalse(actual['single_recorded_source'])

        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN source_url text')
        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN raw_hash text')
        self.conn.execute('UPDATE actual.tge_fix1 SET source_url=%s, raw_hash=%s',
                          ('https://example.test/fix1', 'a' * 64))
        self.conn.execute('UPDATE actual.tge_fix1 SET raw_hash=upper(raw_hash) WHERE hour=2')
        actual = report()
        self.assertTrue(actual['single_recorded_source'])
        self.assertEqual(actual['distinct_recorded_sources'], 1)
        self.assertEqual(actual['quality_status'], 'UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION')
        self.assertNotIn('https://example.test', str(actual))

        self.conn.execute('UPDATE actual.tge_fix1 SET raw_hash=%s WHERE hour=24', ('b' * 64,))
        self.conn.execute('UPDATE actual.tge_fix1 SET source_url=%s WHERE hour=23',
                          ('https://example.test/other',))
        self.conn.execute('UPDATE actual.tge_fix1 SET raw_hash=%s WHERE hour=22', ('invalid',))
        actual = report()
        self.assertFalse(actual['single_recorded_source'])
        self.assertEqual(actual['distinct_recorded_sources'], 3)
        self.assertEqual(actual['rows_without_usable_source_metadata'], 1)

    def test_fix1_training_timing_uses_latest_retrieval_and_warsaw_dst(self):
        day = date(2026, 10, 25)
        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN first_seen_at_utc timestamptz')
        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN retrieved_at_utc timestamptz')
        with self.conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO actual.tge_fix1 VALUES (%s,%s,100,%s,%s)',
                [(day, hour, '2026-10-24T08:00:00Z', '2026-10-25T08:50:00Z')
                 for hour in range(1, 26)],
            )

        def timing():
            actual, = audit_connection(self.conn)['actual_days']
            self.assertEqual(actual['next_delivery_issue_cutoff_utc'], '2026-10-25T08:50:00+00:00')
            self.assertEqual(actual['quality_status'], 'UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION')
            return actual['next_delivery_training_timing']

        self.assertEqual(timing(), 'TIMING_CANDIDATE')
        self.conn.execute(
            'UPDATE actual.tge_fix1 SET retrieved_at_utc=%s WHERE hour=25',
            ('2026-10-25T08:50:01Z',),
        )
        self.assertEqual(timing(), 'AFTER_NEXT_ISSUE_CUTOFF')
        self.conn.execute('UPDATE actual.tge_fix1 SET retrieved_at_utc=NULL WHERE hour=25')
        self.assertEqual(timing(), 'UNPROVEN')

        self.conn.execute(
            'UPDATE actual.tge_fix1 SET retrieved_at_utc=%s, first_seen_at_utc=%s',
            ('2026-10-24T07:50:00Z', '2026-10-24T07:50:00Z'),
        )
        self.assertEqual(timing(), 'AT_OR_BEFORE_OWN_ISSUE_CUTOFF')

    def test_reconcile_fix1_with_tge_reference_inside_readonly_audit(self):
        from decimal import Decimal
        from pathlib import Path
        from shadow_history_audit import read_csv

        fixture = Path(__file__).parent / 'fixtures' / 'tge_fix1_2026-11-03_reference.csv'
        reference = read_csv(fixture)
        day = date(2026, 11, 3)
        with self.conn.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO actual.tge_fix1 VALUES (%s,%s,%s)',
                [(day, int(row['hour'][1:]), Decimal(row['price_pln_mwh'])) for row in reference],
            )
        from psycopg.types.json import Jsonb

        self.conn.execute(
            'ALTER TABLE actual.tge_fix1 ADD COLUMN first_seen_at_utc timestamptz, '
            'ADD COLUMN retrieved_at_utc timestamptz, ADD COLUMN source_url text, ADD COLUMN raw_hash text'
        )
        self.conn.execute(
            'UPDATE actual.tge_fix1 SET first_seen_at_utc=%s, retrieved_at_utc=%s, source_url=%s, raw_hash=%s',
            ('2026-11-02T10:00:00Z', '2026-11-02T10:00:00Z', 'https://example.test/fix1', 'a' * 64),
        )
        self.conn.execute(
            'ALTER TABLE raw.snapshots ADD COLUMN created_at timestamptz, ADD COLUMN source_version text'
        )
        pk5 = [{'period_start_utc': period.isoformat(), 'fcst_gen_unit_stor_prov': 1000,
                'publication_ts': '2026-11-02T08:00:00Z'} for period in expected_periods(day)]
        self.conn.execute(
            'INSERT INTO raw.snapshots VALUES (%s,%s,%s,%s,%s,%s)',
            (1, day, '2026-11-02T08:30:00Z', Jsonb({'pk5l-wp': pk5}),
             '2026-11-02T08:40:00Z', 'energy-collector-v3'),
        )
        report = audit_connection(self.conn, start_day=day, end_day=day, reference_rows=reference)
        self.assertEqual(report['provisional_comparison_coverage']['paired_days'], 1)
        self.assertEqual(report['reference_matched_comparison_coverage']['paired_days'], 1)
        self.assertEqual(report['fix1_reference']['matched_days'], 1)
        self.assertEqual(report['fix1_reference']['compared_periods'], 24)
        self.assertEqual(report['fix1_reference']['different_periods'], 0)
        self.assertEqual(report['actual_days'][0]['quality_status'],
                         'UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION')

        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN period_start_utc timestamptz')
        self.conn.execute('UPDATE actual.tge_fix1 SET period_start_utc=%s WHERE hour=1',
                          (expected_periods(day)[0],))
        partial = audit_connection(self.conn, reference_rows=reference)
        self.assertFalse(partial['actual_days'][0]['structurally_complete'])
        self.assertEqual(partial['fix1_reference']['missing_or_invalid_history_days'], [day.isoformat()])
        self.conn.execute('UPDATE actual.tge_fix1 SET period_start_utc=NULL WHERE hour=1')

        self.conn.execute('UPDATE actual.tge_fix1 SET price_pln_mwh=price_pln_mwh+1 WHERE hour=24')
        report = audit_connection(self.conn, reference_rows=reference)
        self.assertEqual(report['provisional_comparison_coverage']['paired_days'], 0)
        self.assertEqual(report['reference_matched_comparison_coverage']['paired_days'], 0)
        unreferenced = audit_connection(self.conn)
        self.assertEqual(unreferenced['provisional_comparison_coverage']['paired_days'], 1)
        self.assertNotIn('reference_matched_comparison_coverage', unreferenced)
        comparison = report['fix1_reference']
        self.assertEqual(comparison['matched_days'], 0)
        self.assertEqual(comparison['compared_days'][0]['different_periods_utc'],
                         ['2026-11-03T22:00:00+00:00'])
        with self.assertRaisesRegex(ValueError, 'outside the audit range'):
            audit_connection(self.conn, end_day=date(2026, 11, 2), reference_rows=reference)

    def test_audit_accepts_explicit_utc_periods_without_legacy_hour_column(self):
        self.conn.execute(
            'ALTER TABLE actual.tge_fix1 DROP COLUMN hour, ADD COLUMN period_start_utc timestamptz'
        )
        reference = []
        for day in (date(2026, 3, 29), date(2026, 10, 25), date(2026, 11, 2)):
            prices = list(enumerate(expected_periods(day), 1))
            with self.conn.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO actual.tge_fix1 (delivery_date, price_pln_mwh, period_start_utc) '
                    'VALUES (%s,%s,%s)',
                    [(day, price, period) for price, period in prices],
                )
            reference.extend(dict(delivery_date=day.isoformat(), period_start_utc=period.isoformat(),
                                  fix1=str(price)) for price, period in prices)

        report = audit_connection(self.conn, reference_rows=reference)
        self.assertEqual([day['expected_periods'] for day in report['actual_days']], [23, 25, 24])
        self.assertTrue(all(day['structurally_complete'] and day['period_basis'] == 'EXPLICIT_UTC'
                            and day['distinct_legacy_hours'] == 0 for day in report['actual_days']))
        self.assertEqual(report['fix1_reference']['matched_days'], 3)
        self.assertEqual(report['fix1_reference']['compared_periods'], 72)

    def test_reconcile_dst_days_requires_explicit_distinct_utc_periods(self):
        self.conn.execute('ALTER TABLE actual.tge_fix1 ADD COLUMN period_start_utc timestamptz')
        reference = []
        for day, count in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            periods = expected_periods(day)
            self.assertEqual(len(periods), count)
            with self.conn.cursor() as cursor:
                cursor.executemany(
                    'INSERT INTO actual.tge_fix1 VALUES (%s,%s,%s,%s)',
                    [(day, period.astimezone(WARSAW).hour + 1, price, period)
                     for price, period in enumerate(periods, 1)],
                )
            reference.extend(dict(delivery_date=day.isoformat(), period_start_utc=period.isoformat(),
                                  fix1=str(price)) for price, period in enumerate(periods, 1))

        report = audit_connection(self.conn, reference_rows=reference)
        self.assertTrue(all(day['structurally_complete'] and day['period_basis'] == 'EXPLICIT_UTC'
                            for day in report['actual_days']))
        matched = report['fix1_reference']
        self.assertEqual(matched['matched_days'], 2)
        self.assertEqual(matched['compared_periods'], 48)

        self.conn.execute(
            'UPDATE actual.tge_fix1 SET period_start_utc=NULL WHERE delivery_date=%s AND hour=1',
            (date(2026, 3, 29),),
        )
        autumn = date(2026, 10, 25)
        periods = expected_periods(autumn)
        self.conn.execute(
            'UPDATE actual.tge_fix1 SET period_start_utc=%s WHERE delivery_date=%s AND period_start_utc=%s',
            (periods[2], autumn, periods[3]),
        )
        report = audit_connection(self.conn, reference_rows=reference)
        self.assertTrue(all(not day['structurally_complete'] for day in report['actual_days']))
        unmatched = report['fix1_reference']
        self.assertEqual(unmatched['matched_days'], 0)
        self.assertEqual(unmatched['missing_or_invalid_history_days'], ['2026-03-29', '2026-10-25'])
