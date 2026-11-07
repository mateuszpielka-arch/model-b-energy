import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_archive_batch import prepare_batch
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_postgres_scores import initialize_scores, score_archived_day
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures/tge_history_2026-11-04_2026-11-06'
NOV_7_FIXTURES = FIXTURES.with_name('tge_delivery_2026-11-07')
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
            self.assertEqual(archive_batch(conn, revised, revised_raw), 'QUARANTINE')
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

    def test_score_fixed_forecast_against_archived_official_prices(self):
        entry = json.loads((FIXTURES / 'manifest.json').read_text())['entries'][0]
        raw, _, rows = load_session(entry)
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
