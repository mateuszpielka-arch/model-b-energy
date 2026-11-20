import copy
from datetime import date, datetime, timedelta
import gzip
import json
from pathlib import Path
import unittest

from shadow_asof import cutoff_for_day
from shadow_ingest_manifest import load_batches
from shadow_source_history import prepare_history_collection, prepare_history_report_from_captures
from shadow_walkforward_csv import evaluate_csv_rows


FIXTURES = Path(__file__).parent / 'fixtures'


def package(day, pk5_name):
    actual_directory = FIXTURES / ('tge_delivery_' + day)
    (batch, raw), = load_batches(actual_directory / 'manifest.json')
    entry, = json.loads((actual_directory / 'manifest.json').read_text())['entries']
    pk5_directory = FIXTURES / pk5_name
    metadata = json.loads((pk5_directory / 'capture.json').read_text())
    return dict(
        actual_batch=batch, actual_raw=raw,
        actual_reference=(actual_directory / entry['reference_file']).read_bytes(),
        pk5_captures=[dict(
            raw=gzip.decompress((pk5_directory / metadata['raw_file']).read_bytes()),
            source_url=metadata['url'],
            retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
            retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
        )],
    )


class SourceHistoryCollectionTests(unittest.TestCase):
    def inputs(self):
        return [
            package('2026-11-20', 'pse_pk5_delivery_2026-11-20_second'),
            package('2026-11-21', 'pse_pk5_delivery_2026-11-21_third'),
        ]

    def test_real_days_combine_in_date_order_with_evidence_and_shared_audit(self):
        days = self.inputs()
        before = copy.deepcopy(days)
        report = prepare_history_collection(days=days)
        self.assertEqual(report, prepare_history_collection(days=list(reversed(days))))
        self.assertEqual(report['days'], [prepare_history_report_from_captures(**day) for day in days])
        self.assertEqual(report['periods'], 48)
        self.assertEqual(len({row['period_start_utc'] for row in report['rows']}), 48)
        self.assertEqual({row['delivery_date'] for row in report['rows'][:24]}, {'2026-11-20'})
        self.assertEqual({row['delivery_date'] for row in report['rows'][24:]}, {'2026-11-21'})
        audit = report['history_audit']
        self.assertEqual((audit['complete_pair_days'], audit['model_ready_days'],
                          audit['longest_model_ready_run']), (2, 2, 2))
        self.assertEqual(audit['missing_days_for_first_comparison'], {'30D': 29, '60D': 59, '90D': 89})
        self.assertEqual(len(json.loads(json.dumps(report))['rows']), 48)
        evaluation = evaluate_csv_rows(
            report['rows'], evaluated_at=days[1]['actual_batch'].retrieved_at,
            target_from=date(2026, 11, 20), target_to=date(2026, 11, 21),
        )
        self.assertEqual(evaluation['csv_admission']['admitted_days'], 2)
        self.assertEqual(evaluation['walkforward']['common_periods'], 0)
        self.assertEqual(evaluation['walkforward']['ranking'], [])
        self.assertEqual(days, before)

    def test_late_day_rejects_collection_in_both_orders_without_changing_inputs(self):
        first, second = self.inputs()
        late = copy.deepcopy(second)
        cutoff = cutoff_for_day(date(2026, 11, 21))
        late['pk5_captures'][0].update(
            retrieved_at=cutoff + timedelta(microseconds=1),
            retrieval_completed_at=cutoff + timedelta(microseconds=2),
        )
        before = copy.deepcopy((first, late))
        for days in ([first, late], [late, first]):
            with self.assertRaisesRegex(ValueError, 'RETRIEVAL_STARTED_AFTER_CUTOFF'):
                prepare_history_collection(days=days)
        self.assertEqual((first, late), before)
        self.assertEqual(prepare_history_collection(days=[first])['periods'], 24)

    def test_duplicate_delivery_and_malformed_collections_are_rejected(self):
        first, _ = self.inputs()
        for days in ([first, first], [first, copy.deepcopy(first)]):
            with self.assertRaisesRegex(ValueError, 'Duplicate delivery date'):
                prepare_history_collection(days=days)
        for days in (None, [], {}, first, [None], [{**first, 'extra': True}]):
            with self.subTest(kind=type(days).__name__):
                with self.assertRaisesRegex(ValueError, 'source day packages|Source day package'):
                    prepare_history_collection(days=days)
