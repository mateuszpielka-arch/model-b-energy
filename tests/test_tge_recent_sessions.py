import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
import unittest

from shadow_archive_batch import prepare_batch
from shadow_asof import cutoff_for_day
from shadow_ingest_manifest import ingest_manifest
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_postgres_scores import initialize_scores, score_archived_day
from shadow_scoring import expected_periods
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures/tge_history_2026-11-04_2026-11-06'
NOV_7_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-07')
NOV_8_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-08')
NOV_9_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-09')
NOV_10_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-10')
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def load_session(entry, directory=FIXTURES):
    raw = gzip.decompress((directory / entry['raw_file']).read_bytes())
    reference = (directory / entry['reference_file']).read_bytes()
    return raw, reference, list(csv.DictReader(reference.decode('utf-8').splitlines()))


def prepare_session(entry, raw, reference_rows):
    return prepare_batch(
        raw=raw,
        day=entry['delivery_date'],
        source_url=entry['url'],
        retrieved_at=datetime.fromisoformat(entry['retrieved_at_utc']),
        reference_rows=reference_rows,
        reference_sha256=entry['reference_sha256'],
    )


class RecentOfficialSessionsTests(unittest.TestCase):
    def test_captured_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(FIXTURES, ['2026-11-04', '2026-11-05', '2026-11-06'])

    def test_saturday_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(NOV_7_FIXTURES, ['2026-11-07'])

    def test_sunday_fixing_i_prices_pass_parser_and_archive_admission(self):
        self.check_sessions(NOV_8_FIXTURES, ['2026-11-08'])

    def test_monday_fixing_i_prices_pass_parser_and_manifest_admission(self):
        self.check_sessions(NOV_9_FIXTURES, ['2026-11-09'])
        report = ingest_manifest(NOV_9_FIXTURES / 'manifest.json')
        self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 1})
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_first_standard_day_after_autumn_change_matches_official_tge(self):
        day = date(2026, 10, 26)
        directory = FIXTURES.with_name(f'tge_delivery_{day}')
        self.check_sessions(directory, [day.isoformat()])
        manifest = directory / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, directory)
        self.assertEqual(
            [row['period_start_utc'] for row in rows],
            [period.isoformat() for period in expected_periods(day)],
        )
        self.assertEqual(rows[0]['period_start_utc'], '2026-10-25T23:00:00+00:00')
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def test_tuesday_fixing_i_prices_and_utc_match_official_tge(self):
        day = date(2026, 11, 10)
        self.check_sessions(NOV_10_FIXTURES, [day.isoformat()])
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, _, rows = load_session(entry, NOV_10_FIXTURES)
        self.assertEqual(
            [row['period_start_utc'] for row in rows],
            [period.isoformat() for period in expected_periods(day)],
        )
        report = ingest_manifest(manifest)
        self.assertEqual((report['periods'], report['inserted_periods']), (24, 0))

    def check_sessions(self, directory, days):
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertEqual(manifest['errors'], [])
        self.assertEqual([entry['delivery_date'] for entry in manifest['entries']], days)
        for entry in manifest['entries']:
            day = entry['delivery_date']
            with self.subTest(delivery_date=day):
                session = date.fromisoformat(day) - timedelta(days=1)
                self.assertEqual(entry['session_date'], session.isoformat())
                self.assertEqual(entry['http_status'], 200)
                raw, reference, rows = load_session(entry, directory)
                self.assertEqual(len(raw), entry['bytes'])
                self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
                self.assertEqual(sha256(reference).hexdigest(), entry['reference_sha256'])

                self.assertEqual([row['delivery_date'] for row in rows], [day] * 24)
                expected = [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows]
                self.assertEqual(extract_reference(raw, day), expected)
                actual = parse_complete_standard_day(raw.decode('utf-8'), day)
                self.assertEqual(
                    [(f'H{hour:02d}', Decimal(str(price))) for hour, price in sorted(actual.items())],
                    expected,
                )
                batch = prepare_session(entry, raw, expected)
                self.assertEqual(len(batch.rows), 24)
                self.assertEqual(batch.raw_sha256, entry['raw_sha256'])


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class RecentOfficialSessionsPostgresTests(unittest.TestCase):
    def isolated_connection(self):
        conn = connect_test_database(DSN)
        self.addCleanup(conn.close)
        initialize(conn)
        conn.execute('''TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices,
                        fix1_shadow_test.batches RESTART IDENTITY CASCADE''')
        return conn

    def test_monday_manifest_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_9_FIXTURES)

    def test_tuesday_manifest_with_utc_is_archived_and_replayed_without_duplicate_rows(self):
        self.check_manifest_archive_and_replay(NOV_10_FIXTURES)

    def check_manifest_archive_and_replay(self, directory):
        manifest_path = directory / 'manifest.json'
        entry, = json.loads(manifest_path.read_text())['entries']
        raw, _, rows = load_session(entry, directory)
        day = date.fromisoformat(entry['delivery_date'])
        expected = list(zip(expected_periods(day), (Decimal(row['price_pln_mwh']) for row in rows)))

        with self.isolated_connection() as conn:
            for status, inserted in (('INSERT', 24), ('REPLAY', 0)):
                report = ingest_manifest(manifest_path, conn=conn)
                self.assertEqual(report['days_by_status'], {status: 1})
                self.assertEqual((report['periods'], report['inserted_periods']), (24, inserted))
                self.assertEqual(conn.execute('''
                    SELECT period_start_utc, price FROM fix1_shadow_test.prices
                    WHERE delivery_date = %s ORDER BY period_start_utc
                ''', (day,)).fetchall(), expected)
                self.assertEqual(conn.execute('''
                    SELECT delivery_date, raw_sha256, reference_sha256, source_url, retrieved_at, raw_html
                    FROM fix1_shadow_test.batches
                ''').fetchall(), [(
                    day, entry['raw_sha256'], entry['reference_sha256'], entry['url'],
                    datetime.fromisoformat(entry['retrieved_at_utc']), raw,
                )])
                self.assertEqual(conn.execute(
                    'SELECT count(*) FROM fix1_shadow_test.quarantine'
                ).fetchone(), (0,))

    def test_quarantined_capture_cannot_repopulate_a_missing_day(self):
        manifest = NOV_10_FIXTURES / 'manifest.json'
        with self.isolated_connection() as conn:
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            conn.execute('''
                DELETE FROM fix1_shadow_test.prices
                WHERE period_start_utc = (SELECT min(period_start_utc) FROM fix1_shadow_test.prices)
            ''')
            report = ingest_manifest(manifest, conn=conn)
            self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (23,))

            quarantine_query = 'SELECT batch_id, reason, created_at FROM fix1_shadow_test.quarantine'
            quarantine = conn.execute(quarantine_query).fetchall()
            self.assertEqual(len(quarantine), 1)
            conn.execute('DELETE FROM fix1_shadow_test.prices')
            for _ in range(2):
                report = ingest_manifest(manifest, conn=conn)
                self.assertEqual(report['days_by_status'], {'QUARANTINE': 1})
                self.assertEqual(report['inserted_periods'], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (0,))
            self.assertEqual(conn.execute(quarantine_query).fetchall(), quarantine)
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (1,))

    def test_archive_and_replay_three_sessions_with_original_evidence(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        with self.isolated_connection() as conn:
            for entry in manifest['entries']:
                with self.subTest(delivery_date=entry['delivery_date']):
                    raw, _, rows = load_session(entry)
                    reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
                    batch = prepare_session(entry, raw, reference_rows)
                    self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
                    self.assertEqual(archive_batch(conn, batch, raw), 'REPLAY')

                    stored = conn.execute('''
                        SELECT price.period_start_utc, price.price
                        FROM fix1_shadow_test.prices AS price
                        JOIN fix1_shadow_test.batches AS batch ON batch.id = price.batch_id
                        WHERE price.delivery_date = %s AND batch.delivery_date = %s
                        ORDER BY price.period_start_utc
                    ''', (batch.delivery_date, batch.delivery_date)).fetchall()
                    self.assertEqual(stored, list(batch.rows))

                    evidence = conn.execute('''
                        SELECT raw_sha256, reference_sha256, source_url, retrieved_at, raw_html
                        FROM fix1_shadow_test.batches WHERE delivery_date = %s
                    ''', (batch.delivery_date,)).fetchall()
                    self.assertEqual(evidence, [(
                        entry['raw_sha256'], entry['reference_sha256'], entry['url'],
                        datetime.fromisoformat(entry['retrieved_at_utc']), raw,
                    )])

            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.prices').fetchone(), (72,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))

    def test_conflicting_revision_is_quarantined_once_with_its_own_evidence(self):
        from lxml import html

        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        day = entry['delivery_date']
        raw, reference, rows = load_session(entry)
        original = prepare_session(entry, raw, [(row['hour'], row['price_pln_mwh']) for row in rows])

        # Simulate a revision by changing only the first Fixing I price.
        page = html.fromstring(raw)
        cells = page.xpath(
            '//table[@id="rdn"]/tbody/tr[normalize-space(td[1])=$period]/td[3]',
            period=f'{day}_H01',
        )
        self.assertEqual(len(cells), 1)
        revised_price = Decimal(rows[0]['price_pln_mwh']) + Decimal('0.01')
        cells[0].text = format(revised_price, 'f').replace('.', ',')
        revised_raw = html.tostring(page, encoding='utf-8')
        old_row = f"{day},H01,{rows[0]['price_pln_mwh']}\n".encode()
        self.assertEqual(reference.count(old_row), 1)
        revised_reference = reference.replace(old_row, f'{day},H01,{revised_price}\n'.encode())
        revised_rows = list(csv.DictReader(revised_reference.decode('utf-8').splitlines()))
        revised = prepare_session(
            {**entry, 'reference_sha256': sha256(revised_reference).hexdigest()},
            revised_raw,
            [(row['hour'], row['price_pln_mwh']) for row in revised_rows],
        )
        self.assertEqual(revised.rows, ((original.rows[0][0], revised_price), *original.rows[1:]))

        with self.isolated_connection() as conn:
            self.assertEqual(archive_batch(conn, original, raw), 'INSERT')
            initialize_scores(conn)
            cutoff = cutoff_for_day(original.delivery_date)
            inputs = dict(
                day=original.delivery_date, forecast=original.rows,
                forecast_id='before-conflicting-revision', model_version='synthetic-test',
                forecast_status='SHADOW', issued_at=cutoff,
                publication_ts=cutoff - timedelta(minutes=10), retrieved_at=cutoff,
                reference_csv=reference,
            )
            self.assertEqual(score_archived_day(conn, **inputs)[0], 'INSERT')
            score_query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            original_score = conn.execute(score_query).fetchall()
            with connect_test_database(DSN) as scorer, ThreadPoolExecutor(max_workers=1) as pool:
                scorer.execute("SET statement_timeout = '10s'")
                scorer_pid = scorer.info.backend_pid
                with conn.transaction():
                    self.assertEqual(archive_batch(conn, revised, revised_raw), 'QUARANTINE')
                    pending = pool.submit(score_archived_day, scorer, **inputs)
                    deadline = monotonic() + 5
                    while conn.info.backend_pid not in conn.execute(
                        'SELECT pg_blocking_pids(%s)', (scorer_pid,),
                    ).fetchone()[0]:
                        self.assertFalse(pending.done(), 'Scoring completed before quarantine committed')
                        self.assertLess(monotonic(), deadline, 'Scoring did not wait for the archive lock')
                        sleep(0.02)
                with self.assertRaisesRegex(ValueError, 'Quarantined delivery day'):
                    pending.result(timeout=5)
            self.assertEqual(conn.execute(score_query).fetchall(), original_score)
            self.assertEqual(archive_batch(conn, revised, revised_raw), 'QUARANTINE')
            self.assertEqual(archive_batch(conn, original, raw), 'REPLAY')

            stored = conn.execute('''
                SELECT p.period_start_utc, p.price, b.raw_sha256
                FROM fix1_shadow_test.prices AS p
                JOIN fix1_shadow_test.batches AS b ON b.id = p.batch_id
                ORDER BY p.period_start_utc
            ''').fetchall()
            self.assertEqual(stored, [(period, price, original.raw_sha256) for period, price in original.rows])

            quarantined = conn.execute('''
                SELECT b.raw_sha256, b.reference_sha256, b.raw_html
                FROM fix1_shadow_test.quarantine AS q
                JOIN fix1_shadow_test.batches AS b ON b.id = q.batch_id
            ''').fetchall()
            self.assertEqual(quarantined, [(revised.raw_sha256, revised.reference_sha256, revised_raw)])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))

    def test_score_monday_manifest_at_capture_time_against_csv_metrics(self):
        manifest_path = NOV_9_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest_path.read_text())['entries']
        day = date.fromisoformat(entry['delivery_date'])
        cutoff = cutoff_for_day(day)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        reference = (NOV_9_FIXTURES / entry['reference_file']).read_bytes()
        inputs = dict(
            day=day,
            forecast=[(period, Decimal('1500')) for period in expected_periods(day)],
            forecast_id='monday-constant-1500-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Exact-fraction oracle calculated directly from the reference CSV.
        expected = {
            'mae': '638.960833333333',
            'mape_pct': '47.705932238053',
            'wape_pct': '41.476845813844',
            'bias': '-40.524166666667',
        }
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest_path, conn=conn)['inserted_periods'], 24)
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **inputs, evaluated_at=captured - timedelta(seconds=1))
            count = 'SELECT count(*) FROM fix1_shadow_test.scores'
            self.assertEqual(conn.execute(count).fetchone(), (0,))
            decision, score = score_archived_day(conn, **inputs, evaluated_at=captured)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual((score.periods, score.mape_periods), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            self.assertEqual(
                score_archived_day(conn, **inputs, evaluated_at=captured), ('REPLAY', score),
            )
            self.assertEqual(conn.execute(count).fetchone(), (1,))

    def test_score_tuesday_manifest_rejects_shifted_utc_even_with_matching_hash(self):
        day = date(2026, 11, 10)
        manifest = NOV_10_FIXTURES / 'manifest.json'
        entry, = json.loads(manifest.read_text())['entries']
        _, reference, rows = load_session(entry, NOV_10_FIXTURES)
        captured = datetime.fromisoformat(entry['retrieved_at_utc'])
        cutoff = cutoff_for_day(day)
        inputs = dict(
            day=day,
            forecast=[
                (period, Decimal(600 + 25 * index))
                for index, period in enumerate(expected_periods(day))
            ],
            forecast_id='tuesday-synthetic-ramp-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            evaluated_at=captured,
        )
        # Independent exact-fraction calculation against the official CSV prices.
        expected = {
            'mae': '1057.189583333333',
            'mape_pct': '48.998826332563',
            'wape_pct': '55.239844629875',
            'bias': '-1026.317083333333',
        }
        original = rows[0]['period_start_utc'].encode()
        shifted = rows[1]['period_start_utc'].encode()
        changed_reference = reference.replace(original, shifted, 1)
        self.assertNotEqual(changed_reference, reference)

        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(ingest_manifest(manifest, conn=conn)['inserted_periods'], 24)
            decision, score = score_archived_day(conn, **inputs, reference_csv=reference)
            self.assertEqual(decision, 'INSERT')
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), Decimal(value))
            query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)

            # Update only the archived hash in the disposable test DB.
            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Fix1 reference UTC period does not match H01'):
                score_archived_day(conn, **inputs, reference_csv=changed_reference)
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_score_fixed_forecast_against_archived_official_prices(self):
        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        raw, reference, rows = load_session(entry)
        batch = prepare_session(entry, raw, [(row['hour'], row['price_pln_mwh']) for row in rows])
        # Synthetic forecast and timestamps. Only actual prices come from TGE.
        cutoff = datetime(2026, 11, 3, 8, 50, tzinfo=timezone.utc)
        inputs = dict(
            day=batch.delivery_date,
            forecast=[(period, Decimal('1000')) for period, _ in batch.rows],
            forecast_id='constant-1000-official-actual-test',
            model_version='synthetic-test',
            forecast_status='SHADOW',
            issued_at=cutoff,
            publication_ts=cutoff - timedelta(minutes=10),
            retrieved_at=cutoff,
            reference_csv=reference,
        )
        # Independently calculated from the reference CSV using exact fractions.
        expected = {
            'mae': Decimal('368.701666666667'),
            'mape_pct': Decimal('22.311982605236'),
            'wape_pct': Decimal('27.201755896023'),
            'bias': Decimal('-355.433333333333'),
        }
        precision = Decimal('0.000000000001')
        with self.isolated_connection() as conn:
            initialize_scores(conn)
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            decision, score = score_archived_day(conn, **inputs)
            self.assertEqual(decision, 'INSERT')
            self.assertEqual(score_archived_day(conn, **inputs), ('REPLAY', score))

            query = '''
                SELECT s.payload, b.raw_sha256
                FROM fix1_shadow_test.scores AS s
                JOIN fix1_shadow_test.batches AS b ON b.id = s.batch_id
            '''
            stored = conn.execute(query).fetchall()
            self.assertEqual(len(stored), 1)
            payload, raw_hash = stored[0]
            self.assertEqual(raw_hash, entry['raw_sha256'])
            self.assertEqual((payload['periods'], payload['mape_periods']), (24, 24))
            for metric, value in expected.items():
                with self.subTest(metric=metric):
                    self.assertEqual(Decimal(payload[metric]).quantize(precision), value)

            after_cutoff = cutoff + timedelta(seconds=1)
            with self.assertRaisesRegex(ValueError, 'unavailable before D-1 09:50'):
                score_archived_day(conn, **{**inputs, 'issued_at': after_cutoff, 'retrieved_at': after_cutoff})
            self.assertEqual(conn.execute(query).fetchall(), stored)

            changed_reference = reference.replace(b'H01,983.00', b'H01,983.01', 1)
            self.assertNotEqual(changed_reference, reference)
            with self.assertRaisesRegex(ValueError, 'Archived reference CSV hash mismatch'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})

            conn.execute(
                'UPDATE fix1_shadow_test.batches SET reference_sha256 = %s',
                (sha256(changed_reference).hexdigest(),),
            )
            with self.assertRaisesRegex(ValueError, 'Official reference mismatch.*H01'):
                score_archived_day(conn, **{**inputs, 'reference_csv': changed_reference})
            self.assertEqual(conn.execute(query).fetchall(), stored)

    def test_concurrent_saturday_imports_write_one_official_batch(self):
        manifest = json.loads((NOV_7_FIXTURES / 'manifest.json').read_text())
        entry, = manifest['entries']
        raw, _, rows = load_session(entry, NOV_7_FIXTURES)
        reference_rows = [(row['hour'], row['price_pln_mwh']) for row in rows]
        batch = prepare_session(entry, raw, reference_rows)
        start = Barrier(4, timeout=10)

        def import_batch(_):
            with connect_test_database(DSN) as worker_conn:
                start.wait()
                return archive_batch(worker_conn, batch, raw)

        with self.isolated_connection() as conn:
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertCountEqual(
                    list(pool.map(import_batch, range(4))),
                    ['INSERT', 'REPLAY', 'REPLAY', 'REPLAY'],
                )
            evidence = conn.execute('''
                SELECT id, delivery_date, raw_sha256, reference_sha256, raw_html
                FROM fix1_shadow_test.batches
            ''').fetchall()
            self.assertEqual(len(evidence), 1)
            batch_id, *stored_evidence = evidence[0]
            self.assertEqual(stored_evidence, [
                batch.delivery_date, entry['raw_sha256'], entry['reference_sha256'], raw,
            ])
            prices = conn.execute('''
                SELECT period_start_utc, price, batch_id FROM fix1_shadow_test.prices
                ORDER BY period_start_utc
            ''').fetchall()
            self.assertEqual(prices, [(period, price, batch_id) for period, price in batch.rows])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
