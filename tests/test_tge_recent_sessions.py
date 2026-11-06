import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_archive_batch import prepare_batch
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures/tge_history_2026-11-04_2026-11-06'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def load_session(entry):
    raw = gzip.decompress((FIXTURES / entry['raw_file']).read_bytes())
    reference = (FIXTURES / entry['reference_file']).read_bytes()
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
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        self.assertEqual(manifest['errors'], [])
        days = ['2026-11-04', '2026-11-05', '2026-11-06']
        self.assertEqual([entry['delivery_date'] for entry in manifest['entries']], days)
        for entry in manifest['entries']:
            day = entry['delivery_date']
            with self.subTest(delivery_date=day):
                session = date.fromisoformat(day) - timedelta(days=1)
                self.assertEqual(entry['session_date'], session.isoformat())
                self.assertEqual(entry['http_status'], 200)
                raw, reference, rows = load_session(entry)
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
    def test_archive_and_replay_three_sessions_with_original_evidence(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        with connect_test_database(DSN) as conn:
            initialize(conn)
            conn.execute('''TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices,
                            fix1_shadow_test.batches RESTART IDENTITY CASCADE''')
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
