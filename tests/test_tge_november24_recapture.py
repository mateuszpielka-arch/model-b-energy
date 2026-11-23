"""A later genuine TGE capture may replay prices without replacing accepted evidence."""
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_ingest_manifest import ingest_manifest, load_batches
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
ORIGINAL = FIXTURES / 'tge_delivery_2026-11-24'
RECAPTURE = FIXTURES / 'tge_delivery_2026-11-24_recaptured'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def sources():
    original, = load_batches(ORIGINAL / 'manifest.json')
    recaptured, = load_batches(RECAPTURE / 'manifest.json')
    return original, recaptured


def evidence_bytes():
    return {path: path.read_bytes() for root in (ORIGINAL, RECAPTURE)
            for path in root.iterdir() if path.is_file()}


class November24RecaptureTests(unittest.TestCase):
    def test_later_original_html_has_same_24_prices_csv_and_explicit_utc_mapping(self):
        before = evidence_bytes()
        (first, original_raw), (later, later_raw) = sources()
        metadata = json.loads((RECAPTURE / 'manifest.json').read_text())
        entry, = metadata['entries']
        self.assertEqual(metadata['errors'], [])
        self.assertEqual((entry['http_status'], entry['hours'], entry['bytes']), (200, 24, 425640))
        self.assertEqual(entry['raw_sha256'],
                         'f2bcba1575766fd4f7ee1bc0cd70d660b6cdc1510a2f11b6cabd659cb87f0cff')
        self.assertEqual(sha256(later_raw).hexdigest(), entry['raw_sha256'])
        self.assertEqual(later_raw, gzip.decompress((RECAPTURE / entry['raw_file']).read_bytes()))
        self.assertNotEqual(later_raw, original_raw)
        self.assertGreater(later.retrieved_at, first.retrieved_at)
        self.assertEqual(later.retrieved_at.isoformat(), '2026-11-23T18:17:07.872014+00:00')
        self.assertEqual(first.rows, later.rows)
        self.assertEqual(first.reference_sha256, later.reference_sha256)
        self.assertEqual((ORIGINAL / entry['reference_file']).read_bytes(),
                         (RECAPTURE / entry['reference_file']).read_bytes())
        expected = tuple(value for _, value in later.rows)
        self.assertEqual(tuple(parse_complete_standard_day(
            later_raw.decode(), '2026-11-24', exact=True).values()), expected)
        self.assertEqual(tuple(value for _, value in extract_reference(later_raw, '2026-11-24')),
                         expected)
        self.assertEqual(evidence_bytes(), before)


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class November24RecaptureArchiveTests(unittest.TestCase):
    def test_real_recapture_replays_without_replacing_original_raw_or_availability(self):
        files = evidence_bytes()
        (first, original_raw), (later, later_raw) = sources()
        self.assertNotEqual(first.raw_sha256, later.raw_sha256)
        queries = [
            'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc',
            'SELECT * FROM fix1_shadow_test.batches ORDER BY id',
            'SELECT * FROM fix1_shadow_test.quarantine ORDER BY batch_id',
        ]
        with connect_test_database(DSN) as conn:
            initialize(conn)
            conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                         'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
            self.assertEqual(archive_batch(conn, first, original_raw), 'INSERT')
            before = [conn.execute(query).fetchall() for query in queries]
            for directory in (RECAPTURE, ORIGINAL, RECAPTURE):
                report = ingest_manifest(directory / 'manifest.json', conn=conn)
                self.assertEqual((report['days_by_status'], report['inserted_periods']),
                                 ({'REPLAY': 1}, 0))
                result, = report['results']
                self.assertFalse(result['scoring_blocked_by_quarantine'])
                self.assertEqual(result['accepted_capture'], dict(
                    raw_sha256=first.raw_sha256, retrieved_at_utc=first.retrieved_at.isoformat()))
                self.assertEqual([conn.execute(query).fetchall() for query in queries], before)
            self.assertEqual(conn.execute(
                'SELECT raw_html,retrieved_at FROM fix1_shadow_test.batches',
            ).fetchall(), [(original_raw, first.retrieved_at)])
            self.assertEqual(conn.execute(
                'SELECT period_start_utc,price FROM fix1_shadow_test.prices ORDER BY period_start_utc',
            ).fetchall(), list(first.rows))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (1,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
        self.assertEqual(evidence_bytes(), files)


if __name__ == '__main__':
    unittest.main()
