import csv
from datetime import date, datetime
from decimal import Decimal
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_history_audit import audit_rows
from shadow_ingest_manifest import load_batches
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_scoring import expected_periods
from shadow_source_history import prepare_history_rows
from shadow_walkforward_csv import evaluate_csv_rows
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-11-21'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


class November21SourcesTests(unittest.TestCase):
    def test_real_fix1_parser_xpath_reference_and_utc_agree(self):
        entry, = json.loads((DIRECTORY / 'manifest.json').read_text())['entries']
        (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
        reference = (DIRECTORY / entry['reference_file']).read_bytes()
        rows = list(csv.DictReader(reference.decode().splitlines()))
        self.assertEqual(len(raw), entry['bytes'])
        self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
        self.assertEqual(sha256(reference).hexdigest(), entry['reference_sha256'])
        self.assertEqual(len(rows), 24)
        independent = extract_reference(raw, '2026-11-21')
        self.assertEqual(independent, [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows])
        parsed = parse_complete_standard_day(raw.decode(), '2026-11-21', exact=True)
        self.assertEqual(parsed, {index: price for index, (_, price) in enumerate(independent, 1)})
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(date(2026, 11, 21))])
        self.assertEqual(batch.rows, tuple((period, price) for period, (_, price) in
                                          zip(expected_periods(date(2026, 11, 21)), independent, strict=True)))
        self.assertEqual((parsed[1], parsed[24]), (Decimal('783.16'), Decimal('552.65')))

    def test_two_real_paired_days_are_ready_but_do_not_manufacture_training_windows(self):
        rows = []
        for label, pk5_name in (
            ('2026-11-20', 'pse_pk5_delivery_2026-11-20_second'),
            ('2026-11-21', 'pse_pk5_delivery_2026-11-21_third'),
        ):
            actual_directory = FIXTURES / ('tge_delivery_' + label)
            (batch, actual_raw), = load_batches(actual_directory / 'manifest.json')
            entry, = json.loads((actual_directory / 'manifest.json').read_text())['entries']
            pk5_directory = FIXTURES / pk5_name
            metadata = json.loads((pk5_directory / 'capture.json').read_text())
            day_rows = prepare_history_rows(
                pk5_raw=gzip.decompress((pk5_directory / metadata['raw_file']).read_bytes()),
                pk5_source_url=metadata['url'],
                retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
                actual_batch=batch, actual_raw=actual_raw,
                actual_reference=(actual_directory / entry['reference_file']).read_bytes(),
            )
            self.assertEqual(len(day_rows), 24)
            self.assertEqual({row['actual_available_at'] for row in day_rows},
                             {batch.retrieved_at.isoformat()})
            rows.extend(day_rows)
        self.assertEqual(len({row['period_start_utc'] for row in rows}), 48)
        self.assertEqual((rows[24]['jwcd_mw'], rows[24]['fix1_pln_mwh']), ('8571.00000', '783.16'))
        audit = audit_rows(rows)
        self.assertEqual(audit['complete_pair_days'], 2)
        self.assertEqual(audit['model_ready_days'], 2)
        self.assertEqual(audit['longest_model_ready_run'], 2)
        self.assertEqual(audit['missing_days_for_first_comparison'], {'30D': 29, '60D': 59, '90D': 89})
        report = evaluate_csv_rows(rows, evaluated_at=batch.retrieved_at,
                                   target_from=date(2026, 11, 20), target_to=date(2026, 11, 21))
        self.assertEqual(report['csv_admission']['admitted_days'], 2)
        self.assertEqual(report['walkforward']['ranking'], [])
        self.assertEqual(report['walkforward']['common_periods'], 0)
        self.assertEqual(len(report['walkforward']['skipped']), 2)


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL')
class November21ArchiveTests(unittest.TestCase):
    def test_real_complete_capture_inserts_then_replays_with_immutable_evidence(self):
        (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
        with connect_test_database(DSN) as conn:
            initialize(conn)
            conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                         'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            self.assertEqual(archive_batch(conn, batch, raw), 'REPLAY')
            self.assertEqual(conn.execute(
                'SELECT period_start_utc, price FROM fix1_shadow_test.prices ORDER BY period_start_utc'
            ).fetchall(), list(batch.rows))
            self.assertEqual(conn.execute(
                'SELECT raw_sha256, raw_html, retrieved_at FROM fix1_shadow_test.batches'
            ).fetchall(), [(batch.raw_sha256, raw, batch.retrieved_at)])
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))
