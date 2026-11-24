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
ORIGINAL = FIXTURES / 'tge_delivery_2026-11-25'
RECAPTURE = FIXTURES / 'tge_delivery_2026-11-25_recaptured'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def sources():
    original, = load_batches(ORIGINAL / 'manifest.json')
    recaptured, = load_batches(RECAPTURE / 'manifest.json')
    return original, recaptured


def evidence_bytes():
    return {path: path.read_bytes() for root in (ORIGINAL, RECAPTURE)
            for path in root.iterdir() if path.is_file()}


class November25RecaptureTests(unittest.TestCase):
    def test_later_original_html_has_same_24_prices_csv_and_explicit_utc_mapping(self):
        before = evidence_bytes()
        (first, original_raw), (later, later_raw) = sources()
        metadata = json.loads((RECAPTURE / 'manifest.json').read_text())
        entry, = metadata['entries']
        self.assertEqual(metadata['errors'], [])
        self.assertEqual((entry['http_status'], entry['hours'], entry['bytes']), (200, 24, 426225))
        self.assertEqual(entry['raw_sha256'],
                         'c5d92ab18825e6f5afec5fda2b86ac2f544682aa94af4ade1c8a622bfc72434d')
        self.assertEqual(sha256(later_raw).hexdigest(), entry['raw_sha256'])
        self.assertEqual(later_raw, gzip.decompress((RECAPTURE / entry['raw_file']).read_bytes()))
        self.assertNotEqual(later_raw, original_raw)
        self.assertGreater(later.retrieved_at, first.retrieved_at)
        self.assertEqual(later.retrieved_at.isoformat(), '2026-11-24T18:15:55.492126+00:00')
        self.assertEqual(first.rows, later.rows)
        self.assertEqual(first.reference_sha256, later.reference_sha256)
        self.assertEqual((ORIGINAL / entry['reference_file']).read_bytes(),
                         (RECAPTURE / entry['reference_file']).read_bytes())
        expected = tuple(value for _, value in later.rows)
        self.assertEqual(tuple(parse_complete_standard_day(
            later_raw.decode(), '2026-11-25', exact=True).values()), expected)
        self.assertEqual(tuple(value for _, value in extract_reference(later_raw, '2026-11-25')),
                         expected)
        self.assertEqual(evidence_bytes(), before)


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class November25RecaptureArchiveTests(unittest.TestCase):
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
            replayed = None
            for directory in (RECAPTURE, ORIGINAL, RECAPTURE):
                report = ingest_manifest(directory / 'manifest.json', conn=conn)
                self.assertEqual((report['days_by_status'], report['inserted_periods']),
                                 ({'REPLAY': 1}, 0))
                result, = report['results']
                self.assertFalse(result['scoring_blocked_by_quarantine'])
                self.assertEqual(result['accepted_capture'], dict(
                    raw_sha256=first.raw_sha256, retrieved_at_utc=first.retrieved_at.isoformat()))
                after = [conn.execute(query).fetchall() for query in queries]
                self.assertEqual(after[0], before[0])
                self.assertEqual(after[2], before[2])
                self.assertEqual(after[1][0], before[1][0])
                self.assertEqual(len(after[1]), 2)
                if replayed is None:
                    # Retain the genuinely new raw capture without replacing accepted prices.
                    replayed = after
                else:
                    self.assertEqual(after, replayed)
            self.assertEqual(conn.execute(
                'SELECT raw_html,retrieved_at FROM fix1_shadow_test.batches ORDER BY id',
            ).fetchall(), [(original_raw, first.retrieved_at), (later_raw, later.retrieved_at)])
            self.assertEqual(conn.execute(
                'SELECT period_start_utc,price FROM fix1_shadow_test.prices ORDER BY period_start_utc',
            ).fetchall(), list(first.rows))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.batches').fetchone(), (2,))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
        self.assertEqual(evidence_bytes(), files)

    def test_scoring_keeps_first_accepted_availability_in_both_capture_orders(self):
        """Real recaptures must neither advance nor backdate accepted score evidence."""
        from datetime import timedelta
        from decimal import Decimal
        from test_tge_november25_scores import scoring_case
        from shadow_postgres_scores import initialize_scores, score_archived_day

        files = evidence_bytes()
        first, later = sources()
        _, _, reference, inputs, expected = scoring_case()
        for accepted, other, replay_directory in (
            (first, later, RECAPTURE), (later, first, ORIGINAL),
        ):
            batch, raw = accepted
            other_batch, _ = other
            with self.subTest(accepted_at=batch.retrieved_at), connect_test_database(DSN) as conn:
                initialize(conn)
                initialize_scores(conn)
                conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                             'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
                self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
                with self.assertRaisesRegex(ValueError, 'not yet available'):
                    score_archived_day(
                        conn, **inputs, reference_csv=reference,
                        evaluated_at=batch.retrieved_at - timedelta(microseconds=1))
                status, score = score_archived_day(
                    conn, **inputs, reference_csv=reference, evaluated_at=batch.retrieved_at)
                self.assertEqual(status, 'INSERT')
                self.assertEqual(score.actual_version, ':'.join((
                    batch.delivery_date.isoformat(), batch.raw_sha256,
                    batch.reference_sha256, batch.parser_version)))
                for metric, value in expected.items():
                    self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value)
                score_query = 'SELECT * FROM fix1_shadow_test.scores ORDER BY forecast_id'
                saved_score = conn.execute(score_query).fetchall()
                self.assertEqual(len(saved_score), 1)
                report = ingest_manifest(replay_directory / 'manifest.json', conn=conn)
                self.assertEqual((report['days_by_status'], report['inserted_periods']),
                                 ({'REPLAY': 1}, 0))
                result, = report['results']
                self.assertEqual(result['accepted_capture'], dict(
                    raw_sha256=batch.raw_sha256, retrieved_at_utc=batch.retrieved_at.isoformat()))
                self.assertFalse(result['scoring_blocked_by_quarantine'])
                queries = [
                    score_query,
                    'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc',
                    'SELECT * FROM fix1_shadow_test.batches ORDER BY id',
                    'SELECT * FROM fix1_shadow_test.quarantine ORDER BY batch_id',
                ]
                after_import = [conn.execute(query).fetchall() for query in queries]
                self.assertEqual(after_import[0], saved_score)
                self.assertEqual((len(after_import[1]), len(after_import[2]), len(after_import[3])),
                                 (24, 2, 0))
                for supplied_reference in (reference, None):
                    with self.assertRaisesRegex(ValueError, 'not yet available'):
                        score_archived_day(
                            conn, **inputs, reference_csv=supplied_reference,
                            evaluated_at=batch.retrieved_at - timedelta(microseconds=1))
                    self.assertEqual(score_archived_day(
                        conn, **inputs, reference_csv=supplied_reference,
                        evaluated_at=batch.retrieved_at), ('REPLAY', score))
                    self.assertEqual([conn.execute(query).fetchall() for query in queries],
                                     after_import)
                self.assertEqual(conn.execute(
                    "SELECT payload->>'actual_captured_at_utc' FROM fix1_shadow_test.scores",
                ).fetchall(), [(batch.retrieved_at.isoformat(),)])
                # Both observations are retained, but scores remain tied to the first accepted one.
                self.assertEqual(conn.execute(
                    'SELECT raw_sha256 FROM fix1_shadow_test.batches ORDER BY id',
                ).fetchall(), [(batch.raw_sha256,), (other_batch.raw_sha256,)])
        self.assertEqual(evidence_bytes(), files)


if __name__ == '__main__':
    unittest.main()
