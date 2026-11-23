"""Combine retained real Fix1 days only in disposable test files and PostgreSQL."""
from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from shadow_ingest_manifest import ingest_manifest, load_batches
from shadow_postgres_archive import connect_test_database, initialize


FIXTURES = Path(__file__).parent / 'fixtures'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


@contextmanager
def combined_manifest():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        entries = []
        for day in ('2026-11-22', '2026-11-23'):
            source = FIXTURES / ('tge_delivery_' + day)
            entry, = json.loads((source / 'manifest.json').read_text())['entries']
            for field in ('raw_file', 'reference_file'):
                shutil.copyfile(source / entry[field], root / entry[field])
            entries.append(entry)
        path = root / 'manifest.json'
        path.write_text(json.dumps(dict(entries=list(reversed(entries)), errors=[])))
        yield path


class TwoDayManifestEvidenceTests(unittest.TestCase):
    def test_combined_real_capture_dry_run_validates_48_periods_in_date_order(self):
        with combined_manifest() as manifest:
            files = {path: path.read_bytes() for path in manifest.parent.iterdir()}
            report = ingest_manifest(manifest)
            self.assertTrue(report['dry_run'])
            self.assertEqual((report['days'], report['periods'], report['inserted_periods']), (2, 48, 0))
            self.assertEqual(report['days_by_status'], {'VALIDATED_DRY_RUN': 2})
            self.assertEqual([item['day'] for item in report['results']], ['2026-11-22', '2026-11-23'])
            self.assertEqual({path: path.read_bytes() for path in files}, files)


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class TwoDayManifestArchiveTests(unittest.TestCase):
    def test_last_day_failure_rolls_back_both_real_days_then_replays_without_mutation(self):
        from psycopg.errors import CheckViolation
        with combined_manifest() as manifest, connect_test_database(DSN) as conn:
            initialize(conn)
            conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                         'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
            source = load_batches(manifest)
            self.assertEqual([batch.delivery_date for batch, _ in source],
                             [date(2026, 11, 22), date(2026, 11, 23)])
            conn.execute("""ALTER TABLE fix1_shadow_test.prices
                ADD CONSTRAINT reject_real_two_day_final_period CHECK (
                    delivery_date <> DATE '2026-11-23'
                    OR period_start_utc <> TIMESTAMPTZ '2026-11-23 22:00:00+00')""")
            try:
                with self.assertRaisesRegex(CheckViolation, 'reject_real_two_day_final_period'):
                    ingest_manifest(manifest, conn=conn)
                for table in ('prices', 'batches', 'quarantine'):
                    self.assertEqual(conn.execute(
                        'SELECT count(*) FROM fix1_shadow_test.' + table).fetchone(), (0,))
            finally:
                conn.execute('ALTER TABLE fix1_shadow_test.prices '
                             'DROP CONSTRAINT reject_real_two_day_final_period')
            inserted = ingest_manifest(manifest, conn=conn)
            self.assertEqual(inserted['days_by_status'], {'INSERT': 2})
            self.assertEqual(inserted['inserted_periods'], 48)
            for result, (batch, raw) in zip(inserted['results'], source, strict=True):
                self.assertFalse(result['scoring_blocked_by_quarantine'])
                self.assertEqual(result['accepted_capture'], dict(
                    raw_sha256=batch.raw_sha256, retrieved_at_utc=batch.retrieved_at.isoformat()))
                self.assertEqual(conn.execute(
                    'SELECT period_start_utc,price FROM fix1_shadow_test.prices '
                    'WHERE delivery_date=%s ORDER BY period_start_utc', (batch.delivery_date,),
                ).fetchall(), list(batch.rows))
                self.assertEqual(conn.execute(
                    'SELECT raw_html FROM fix1_shadow_test.batches WHERE raw_sha256=%s',
                    (batch.raw_sha256,),
                ).fetchall(), [(raw,)])
            queries = [
                'SELECT * FROM fix1_shadow_test.prices ORDER BY delivery_date,period_start_utc',
                'SELECT * FROM fix1_shadow_test.batches ORDER BY id',
                'SELECT * FROM fix1_shadow_test.quarantine ORDER BY batch_id',
            ]
            before = [conn.execute(query).fetchall() for query in queries]
            replay = ingest_manifest(manifest, conn=conn)
            self.assertEqual((replay['days_by_status'], replay['inserted_periods']), ({'REPLAY': 2}, 0))
            self.assertEqual([conn.execute(query).fetchall() for query in queries], before)


if __name__ == '__main__':
    unittest.main()
