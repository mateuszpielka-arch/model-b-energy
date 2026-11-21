"""Synthetic scoring control on retained real sources; no issued model forecast."""
import csv
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import json
import os
from pathlib import Path
import unittest

from shadow_asof import cutoff_for_day, select_snapshot
from shadow_ingest_manifest import load_batches
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_postgres_archive import archive_batch, connect_test_database, initialize
from shadow_postgres_scores import initialize_scores, score_archived_day
from shadow_scoring import score_day


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-11-22'
DSN = os.environ.get('FIX1_SHADOW_TEST_DSN')


def scoring_case():
    (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
    reference = (DIRECTORY / 'tge_delivery_2026-11-22.csv').read_bytes()
    directory = FIXTURES / 'pse_pk5_delivery_2026-11-22_fourth'
    metadata = json.loads((directory / 'capture.json').read_text())
    pk5_raw = gzip.decompress((directory / metadata['raw_file']).read_bytes())
    snapshot = prepare_pk5_snapshot(
        raw=pk5_raw, day='2026-11-22', source_url=metadata['url'],
        retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
        retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
    )
    selected = select_snapshot(batch.delivery_date, [snapshot])
    # Fixed synthetic control only; not a trained model or an issued forecast.
    inputs = dict(
        day=batch.delivery_date,
        forecast=[(period, value * Decimal('0.05')) for period, value in selected.rows],
        forecast_id='synthetic-five-percent-nov22:' + selected.snapshot_id,
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



class November22ScoreTests(unittest.TestCase):
    def test_real_sources_control_score_matches_independent_fraction_oracle(self):
        batch, _, _, inputs, expected = scoring_case()
        score = score_day(**inputs, actual=batch.rows, actual_version=batch.raw_sha256,
                          actual_verified=True)
        self.assertEqual((score.periods, score.mape_periods, score.zero_actual_periods), (24, 24, 0))
        self.assertEqual(expected, dict(
            mae=Decimal('195.941666666667'), bias=Decimal('-195.941666666667'),
            mape_pct=Decimal('28.062222694008'), wape_pct=Decimal('31.997332761782'),
        ))
        for metric, value in expected.items():
            self.assertEqual(getattr(score, metric).quantize(Decimal('1e-12')), value, metric)


@unittest.skipUnless(DSN, 'Set FIX1_SHADOW_TEST_DSN for isolated PostgreSQL integration')
class November22PostgresScoreTests(unittest.TestCase):
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



if __name__ == '__main__':
    unittest.main()
