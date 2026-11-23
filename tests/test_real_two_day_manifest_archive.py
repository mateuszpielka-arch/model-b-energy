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


    def test_later_insert_failure_rolls_back_earlier_quarantine_then_preserves_sticky_conflict(self):
        """Fault injection touches only the explicitly isolated test database."""
        from psycopg.errors import CheckViolation
        from shadow_postgres_archive import archive_batch
        with combined_manifest() as manifest, connect_test_database(DSN) as conn:
            initialize(conn)
            conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                         'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
            files = {path: path.read_bytes() for path in manifest.parent.iterdir()}
            first, second = load_batches(manifest)
            batch, raw = first
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            # Corrupt one stored price in the disposable DB to force quarantine.
            conn.execute('UPDATE fix1_shadow_test.prices SET price=price+1 '
                         'WHERE delivery_date=%s AND period_start_utc=%s',
                         (batch.delivery_date, batch.rows[0][0]))
            queries = [
                'SELECT * FROM fix1_shadow_test.prices ORDER BY delivery_date,period_start_utc',
                'SELECT * FROM fix1_shadow_test.batches ORDER BY id',
                'SELECT * FROM fix1_shadow_test.quarantine ORDER BY batch_id',
            ]
            before = [conn.execute(query).fetchall() for query in queries]
            conn.execute("""ALTER TABLE fix1_shadow_test.prices
                ADD CONSTRAINT reject_after_real_quarantine CHECK (
                    delivery_date <> DATE '2026-11-23'
                    OR period_start_utc <> TIMESTAMPTZ '2026-11-23 22:00:00+00')""")
            try:
                with self.assertRaisesRegex(CheckViolation, 'reject_after_real_quarantine'):
                    ingest_manifest(manifest, conn=conn)
                # No quarantine or partial second-day archive survives the failed manifest.
                self.assertEqual([conn.execute(query).fetchall() for query in queries], before)
            finally:
                conn.execute('ALTER TABLE fix1_shadow_test.prices '
                             'DROP CONSTRAINT reject_after_real_quarantine')
            result = ingest_manifest(manifest, conn=conn)
            self.assertEqual((result['days_by_status'], result['inserted_periods']),
                             ({'INSERT': 1, 'QUARANTINE': 1}, 24))
            conflict, accepted = result['results']
            self.assertEqual((conflict['day'], conflict['status']), ('2026-11-22', 'QUARANTINE'))
            self.assertTrue(conflict['scoring_blocked_by_quarantine'])
            self.assertIsNone(conflict['accepted_capture'])
            self.assertEqual((accepted['day'], accepted['status']), ('2026-11-23', 'INSERT'))
            self.assertFalse(accepted['scoring_blocked_by_quarantine'])
            second_batch, second_raw = second
            self.assertEqual(accepted['accepted_capture'], dict(
                raw_sha256=second_batch.raw_sha256,
                retrieved_at_utc=second_batch.retrieved_at.isoformat()))
            self.assertEqual(conn.execute(
                'SELECT * FROM fix1_shadow_test.prices WHERE delivery_date=%s '
                'ORDER BY period_start_utc', (batch.delivery_date,),
            ).fetchall(), before[0])
            self.assertEqual(conn.execute(
                'SELECT period_start_utc,price FROM fix1_shadow_test.prices '
                'WHERE delivery_date=%s ORDER BY period_start_utc', (second_batch.delivery_date,),
            ).fetchall(), list(second_batch.rows))
            self.assertEqual(conn.execute(
                'SELECT raw_html FROM fix1_shadow_test.batches ORDER BY delivery_date',
            ).fetchall(), [(raw,), (second_raw,)])
            self.assertEqual(conn.execute(
                'SELECT reason FROM fix1_shadow_test.quarantine',
            ).fetchall(), [('Existing prices differ or day is partial',)])
            committed = [conn.execute(query).fetchall() for query in queries]
            for _ in range(2):
                replay = ingest_manifest(manifest, conn=conn)
                self.assertEqual((replay['days_by_status'], replay['inserted_periods']),
                                 ({'QUARANTINE': 1, 'REPLAY': 1}, 0))
                self.assertEqual(replay['results'][0], conflict)
                self.assertEqual(replay['results'][1], {**accepted, 'status': 'REPLAY'})
                self.assertEqual([conn.execute(query).fetchall() for query in queries], committed)
            self.assertEqual({path: path.read_bytes() for path in files}, files)


if __name__ == '__main__':
    unittest.main()
