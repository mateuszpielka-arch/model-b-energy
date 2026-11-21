"""Real retained November 22 evidence; writes only to the guarded test database."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_archive_batch import prepare_batch, read_reference_csv
from shadow_ingest_manifest import load_batches
from shadow_postgres_archive import archive_batch, connect_test_database, initialize


DIRECTORY = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-22'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def evidence():
    (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
    entry, = json.loads((DIRECTORY / 'manifest.json').read_text())['entries']
    reference = (DIRECTORY / entry['reference_file']).read_bytes()
    return batch, raw, reference


def conflict_evidence():
    batch, raw, reference = evidence()
    # Deliberate synthetic H01 +1; never an official correction or saved fixture.
    if raw.count(b'356,76') != 1 or reference.count(b'356.76') != 1:
        raise ValueError('Expected unique real H01 price for synthetic conflict')
    changed_raw = raw.replace(b'356,76', b'357,76', 1)
    changed_reference = reference.replace(b'356.76', b'357.76', 1)
    changed = prepare_batch(
        raw=changed_raw, day=batch.delivery_date.isoformat(), source_url=batch.source_url,
        retrieved_at=batch.retrieved_at + timedelta(seconds=1),
        reference_sha256=sha256(changed_reference).hexdigest(),
        reference_rows=read_reference_csv(changed_reference, batch.delivery_date.isoformat(),
                                          require_utc=True),
    )
    return changed, changed_raw


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class November22ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect_test_database(DSN)
        self.addCleanup(self.conn.close)
        initialize(self.conn)
        self.conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                          'fix1_shadow_test.batches RESTART IDENTITY CASCADE')

    def state(self):
        return {
            'prices': self.conn.execute(
                'SELECT * FROM fix1_shadow_test.prices ORDER BY period_start_utc').fetchall(),
            'batches': self.conn.execute(
                'SELECT * FROM fix1_shadow_test.batches ORDER BY id').fetchall(),
            'quarantine': self.conn.execute(
                'SELECT * FROM fix1_shadow_test.quarantine ORDER BY batch_id').fetchall(),
        }

    def test_last_period_failure_rolls_back_entire_real_day_then_retry_replays(self):
        from psycopg.errors import CheckViolation
        batch, raw, _ = evidence()
        self.assertEqual(len(batch.rows), 24)
        # Fail at the final UTC period, after earlier price inserts in this transaction.
        self.conn.execute("""ALTER TABLE fix1_shadow_test.prices
            ADD CONSTRAINT reject_nov22_last_period CHECK (
                delivery_date <> DATE '2026-11-22'
                OR period_start_utc <> TIMESTAMPTZ '2026-11-22 22:00:00+00')""")
        try:
            with self.assertRaisesRegex(CheckViolation, 'reject_nov22_last_period'):
                archive_batch(self.conn, batch, raw)
            self.assertEqual(self.state(), {'prices': [], 'batches': [], 'quarantine': []})
        finally:
            self.conn.execute('ALTER TABLE fix1_shadow_test.prices '
                              'DROP CONSTRAINT reject_nov22_last_period')
        self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
        saved = self.state()
        self.assertEqual(len(saved['batches']), 1)
        self.assertEqual(saved['quarantine'], [])
        self.assertEqual(self.conn.execute(
            'SELECT period_start_utc, price FROM fix1_shadow_test.prices '
            'ORDER BY period_start_utc').fetchall(), list(batch.rows))
        self.assertEqual(self.conn.execute(
            'SELECT raw_sha256, raw_html, retrieved_at FROM fix1_shadow_test.batches'
        ).fetchall(), [(batch.raw_sha256, raw, batch.retrieved_at)])
        self.assertEqual(archive_batch(self.conn, batch, raw), 'REPLAY')
        self.assertEqual(self.state(), saved)

    def test_synthetic_conflict_preserves_real_prices_and_sticky_quarantine(self):
        batch, raw, _ = evidence()
        changed, changed_raw = conflict_evidence()
        self.assertEqual(changed.rows[0][1] - batch.rows[0][1], Decimal('1'))
        self.assertEqual(changed.rows[1:], batch.rows[1:])
        self.assertEqual(archive_batch(self.conn, batch, raw), 'INSERT')
        saved = self.state()
        self.assertEqual(archive_batch(self.conn, changed, changed_raw), 'QUARANTINE')
        quarantined = self.state()
        self.assertEqual(quarantined['prices'], saved['prices'])
        self.assertEqual(quarantined['batches'][:1], saved['batches'])
        self.assertEqual(len(quarantined['batches']), 2)
        self.assertEqual(len(quarantined['quarantine']), 1)
        self.assertEqual(self.conn.execute(
            'SELECT raw_html, reference_sha256 FROM fix1_shadow_test.batches '
            'WHERE raw_sha256 = %s', (changed.raw_sha256,),
        ).fetchall(), [(changed_raw, changed.reference_sha256)])
        for candidate, candidate_raw in ((batch, raw), (changed, changed_raw)):
            self.assertEqual(archive_batch(self.conn, candidate, candidate_raw), 'QUARANTINE')
            self.assertEqual(self.state(), quarantined)


if __name__ == '__main__':
    unittest.main()
