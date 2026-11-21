import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest

from shadow_asof import cutoff_for_day
from shadow_ingest_manifest import load_batches
from shadow_scoring import expected_periods
from shadow_source_history import load_history_day, prepare_history_report_from_captures
from shadow_walkforward_csv import evaluate_csv_rows
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-11-22'
DAY = date(2026, 11, 22)


def package():
    paths = [FIXTURES / ('pse_pk5_delivery_2026-11-22' + suffix) / 'capture.json'
             for suffix in ('', '_second', '_third', '_fourth')]
    return load_history_day(DIRECTORY / 'manifest.json', paths)


class November22SourcesTests(unittest.TestCase):
    def test_real_fix1_parser_xpath_hashes_and_explicit_utc_agree(self):
        manifest = json.loads((DIRECTORY / 'manifest.json').read_text())
        self.assertEqual(manifest['errors'], [])
        entry, = manifest['entries']
        (batch, raw), = load_batches(DIRECTORY / 'manifest.json')
        reference = (DIRECTORY / entry['reference_file']).read_bytes()
        self.assertEqual(len(raw), 280499)
        self.assertEqual(sha256(raw).hexdigest(),
                         '6435b1a6171a4c65bf04a3fa6aaf944e5c492651869d6146c645a2703a63291b')
        self.assertEqual(sha256(reference).hexdigest(),
                         'f8fa69544585150148c84cf9114afb16faf5246c6062ac424f91a5ee7c1b8847')
        self.assertEqual(entry['retrieved_at_utc'], '2026-11-21T10:19:39.063666+00:00')
        rows = list(csv.DictReader(reference.decode().splitlines()))
        independent = extract_reference(raw, DAY.isoformat())
        parsed = parse_complete_standard_day(raw.decode(), DAY.isoformat(), exact=True)
        self.assertEqual(len(rows), 24)
        self.assertEqual(independent, [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows])
        self.assertEqual(parsed, dict(enumerate((price for _, price in independent), 1)))
        self.assertEqual([row['period_start_utc'] for row in rows],
                         [period.isoformat() for period in expected_periods(DAY)])
        self.assertEqual(batch.rows, tuple(zip(expected_periods(DAY),
                                              (price for _, price in independent), strict=True)))
        self.assertEqual((parsed[1], parsed[24]), (Decimal('356.76'), Decimal('686.52')))

    def test_real_fix1_joins_whole_latest_pk5_with_own_pre_cutoff_evidence(self):
        inputs = package()
        report = prepare_history_report_from_captures(**inputs)
        reverse = prepare_history_report_from_captures(
            **{**inputs, 'pk5_captures': list(reversed(inputs['pk5_captures']))})
        self.assertEqual(report, reverse)
        fourth = json.loads((FIXTURES / 'pse_pk5_delivery_2026-11-22_fourth/capture.json').read_text())
        self.assertEqual(report['selected_snapshot_id'], fourth['snapshot_id'])
        self.assertEqual(len(report['pk5_captures']), 4)
        self.assertEqual(len(report['rejected_captures']), 3)
        self.assertEqual({item['reason'] for item in report['rejected_captures']},
                         {'SUPERSEDED_JWCD_PUBLICATIONS'})
        rows = report['rows']
        self.assertEqual(len(rows), 24)
        self.assertEqual((rows[0]['jwcd_mw'], rows[-1]['jwcd_mw']), ('6326.00000', '9744.00000'))
        self.assertEqual((rows[0]['fix1_pln_mwh'], rows[-1]['fix1_pln_mwh']), ('356.76', '686.52'))
        self.assertEqual({row['actual_available_at'] for row in rows},
                         {inputs['actual_batch'].retrieved_at.isoformat()})
        self.assertEqual({row['jwcd_snapshot_id'] for row in rows}, {fourth['snapshot_id']})
        for row in rows:
            self.assertLessEqual(datetime.fromisoformat(row['publication_ts']),
                                 datetime.fromisoformat(row['retrieved_at']))
            self.assertLessEqual(datetime.fromisoformat(row['retrieved_at']),
                                 datetime.fromisoformat(row['retrieval_completed_at']))
            self.assertLessEqual(datetime.fromisoformat(row['retrieval_completed_at']),
                                 cutoff_for_day(DAY))
        self.assertEqual(report['history_audit']['model_ready_days'], 1)

    def test_source_pair_does_not_create_training_history_or_common_ranking(self):
        inputs = package()
        rows = prepare_history_report_from_captures(**inputs)['rows']
        for evaluated_at in (inputs['actual_batch'].retrieved_at - timedelta(microseconds=1),
                             inputs['actual_batch'].retrieved_at):
            report = evaluate_csv_rows(rows, evaluated_at=evaluated_at,
                                       target_from=DAY, target_to=DAY)
            self.assertEqual(report['csv_admission']['admitted_days'], 1)
            self.assertEqual(report['walkforward']['ranking'], [])
            self.assertEqual(report['walkforward']['common_days'], 0)
            self.assertEqual(report['walkforward']['common_periods'], 0)
            self.assertEqual(len(report['walkforward']['skipped']), 1)


if __name__ == '__main__':
    unittest.main()
