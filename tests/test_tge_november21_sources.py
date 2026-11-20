import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from shadow_history_audit import audit_rows
from shadow_ingest_manifest import load_batches
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_scoring import expected_periods, score_day
from shadow_asof import cutoff_for_day, select_snapshot
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_postgres_scores import initialize_scores, score_archived_day
from shadow_source_history import prepare_history_rows
from shadow_walkforward_csv import evaluate_csv_rows
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-11-21'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def scoring_case():
    (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
    reference = (DIRECTORY / 'tge_delivery_2026-11-21.csv').read_bytes()
    directory = FIXTURES / 'pse_pk5_delivery_2026-11-21_third'
    metadata = json.loads((directory / 'capture.json').read_text())
    pk5_raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
    snapshot = prepare_pk5_snapshot(
        raw=pk5_raw, day='2026-11-21', source_url=metadata['url'],
        retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
        retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
    )
    selected = select_snapshot(batch.delivery_date, [snapshot])
    # Fixed synthetic control only; not a trained model or an issued forecast.
    inputs = dict(
        day=batch.delivery_date,
        forecast=[(period, value * Decimal('0.05')) for period, value in selected.rows],
        forecast_id='synthetic-five-percent-nov21:' + selected.snapshot_id,
        model_version='synthetic-jwcd-five-percent-test-v1', forecast_status='SHADOW',
        issued_at=cutoff_for_day(batch.delivery_date),
        publication_ts=selected.latest_publication_ts_utc,
        retrieved_at=selected.retrieval_completed_at_utc,
    )
    # Independent rational oracle joins source PSE labels to the official CSV labels.
    payload = json.loads(pk5_raw, parse_float=Decimal)['value']
    forecasts = {f"H{int(row['period'].split(' - ')[0]) + 1:02d}":
                 Fraction(row['fcst_gen_unit_stor_prov']) / 20 for row in payload}
    pairs = [(forecasts[row['hour']] - Fraction(row['price_pln_mwh']),
              Fraction(row['price_pln_mwh']))
             for row in csv.DictReader(reference.decode().splitlines())]
    nonzero = [(error, actual) for error, actual in pairs if actual]
    absolute_error = sum(abs(error) for error, _ in pairs)
    rational = dict(
        mae=absolute_error / len(pairs), bias=sum(error for error, _ in pairs) / len(pairs),
        mape_pct=100 * sum(abs(error) / abs(actual) for error, actual in nonzero) / len(nonzero),
        wape_pct=100 * absolute_error / sum(abs(actual) for _, actual in pairs),
    )
    with localcontext() as context:
        context.prec = 80
        expected = {key: (Decimal(value.numerator) / value.denominator).quantize(Decimal('1e-12'))
                    for key, value in rational.items()}
    return batch, raw, reference, inputs, expected


class November21SourcesTests(unittest.TestCase):
    def test_real_sources_synthetic_control_score_matches_independent_fraction_oracle(self):
        batch, _, _, inputs, expected = scoring_case()
        score = score_day(**inputs, actual=batch.rows, actual_version=batch.raw_sha256,
                          actual_verified=True)
        self.assertEqual((score.periods, score.mape_periods, score.zero_actual_periods), (24, 24, 0))
        self.assertEqual(expected, dict(
            mae=Decimal('209.985416666667'), bias=Decimal('-209.985416666667'),
            mape_pct=Decimal('34.502647436342'), wape_pct=Decimal('35.376391638237'),
        ))
        for metric, value in expected.items():
            self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value, metric)

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
    def test_real_source_control_scoring_gates_availability_and_replays_once(self):
        batch, raw, reference, inputs, expected = scoring_case()
        query = 'SELECT input_sha256, payload FROM fix1_shadow_test.scores'
        with connect_test_database(DSN) as conn:
            initialize(conn)
            initialize_scores(conn)
            conn.execute('TRUNCATE fix1_shadow_test.quarantine, fix1_shadow_test.prices, '
                         'fix1_shadow_test.batches RESTART IDENTITY CASCADE')
            self.assertEqual(archive_batch(conn, batch, raw), 'INSERT')
            with self.assertRaisesRegex(ValueError, 'not yet available'):
                score_archived_day(conn, **inputs, reference_csv=reference,
                                   evaluated_at=batch.retrieved_at - timedelta(microseconds=1))
            self.assertEqual(conn.execute(query).fetchall(), [])
            status, score = score_archived_day(conn, **inputs, reference_csv=reference,
                                              evaluated_at=batch.retrieved_at)
            self.assertEqual((status, score.periods, score.mape_periods), ('INSERT', 24, 24))
            for metric, value in expected.items():
                self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value, metric)
            saved = conn.execute(query).fetchall()
            self.assertEqual(len(saved), 1)
            for supplied_reference in (reference, None):
                self.assertEqual(score_archived_day(
                    conn, **inputs, reference_csv=supplied_reference,
                    evaluated_at=batch.retrieved_at,
                ), ('REPLAY', score))
                self.assertEqual(conn.execute(query).fetchall(), saved)
            self.assertEqual(conn.execute(
                'SELECT period_start_utc, price FROM fix1_shadow_test.prices ORDER BY period_start_utc'
            ).fetchall(), list(batch.rows))
            self.assertEqual(conn.execute('SELECT count(*) FROM fix1_shadow_test.quarantine').fetchone(), (0,))

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
