import csv
from datetime import date, datetime
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_source_history import prepare_history_collection
from shadow_walkforward_csv import evaluate_csv_rows
from test_shadow_source_history_collection import FIXTURES, package


class SourceCollectionCliTests(unittest.TestCase):
    def groups(self):
        return [
            [str(FIXTURES / 'tge_delivery_2026-11-20/manifest.json'),
             str(FIXTURES / 'pse_pk5_delivery_2026-11-20/capture.json'),
             str(FIXTURES / 'pse_pk5_delivery_2026-11-20_second/capture.json')],
            [str(FIXTURES / 'tge_delivery_2026-11-21/manifest.json'),
             str(FIXTURES / 'pse_pk5_delivery_2026-11-21_third/capture.json')],
        ]

    def run_cli(self, groups, output_format, extra=()):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] /
                                       'shadow_source_collection.py'), '--format', output_format]
        for group in groups:
            command.extend(['--day', *group])
        command.extend(extra)
        return subprocess.run(command, capture_output=True, text=True)


    def test_explicit_source_evaluation_matches_existing_walkforward_without_invented_training(self):
        stamp = '2026-11-20T11:14:16.158611+01:00'
        expected_time = '2026-11-20T10:14:16.158611+00:00'
        baseline = json.loads(self.run_cli(self.groups(), 'json').stdout)
        for groups in (self.groups(), list(reversed(self.groups()))):
            completed = self.run_cli(groups, 'json', ['--walk-forward-at', stamp])
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            evaluation = report.pop('evaluation')
            self.assertEqual(report, baseline)
            self.assertEqual(evaluation.pop('evaluated_at_utc'), expected_time)
            expected = evaluate_csv_rows(
                report['rows'], evaluated_at=datetime.fromisoformat(expected_time),
                target_from=date(2026, 11, 20), target_to=date(2026, 11, 21),
            )
            self.assertEqual(evaluation, json.loads(json.dumps(expected)))
            self.assertEqual(evaluation['csv_admission']['admitted_days'], 2)
            comparison = evaluation['walkforward']
            self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
            self.assertEqual(comparison['ranking'], [])
            self.assertEqual(len(comparison['skipped']), 2)
            self.assertEqual(evaluation['history_audit']['missing_days_for_first_comparison'],
                             {'30D': 29, '60D': 59, '90D': 89})

    def test_evaluation_requires_json_and_full_explicit_timestamp_without_partial_output(self):
        for output_format, stamp, error in (
            ('csv', '2026-11-20T10:14:16+00:00', 'requires --format json'),
            ('json', '2026-11-20T10:14:16', 'invalid'),
            ('json', '2026-11-20T10:14+00:00', 'invalid'),
            ('json', '2026-11-20T10:14:16.1586111+00:00', 'invalid'),
        ):
            with self.subTest(output_format=output_format, stamp=stamp):
                completed = self.run_cli(self.groups(), output_format, ['--walk-forward-at', stamp])
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, '')
                self.assertIn(error, completed.stderr)

    def test_two_real_days_export_in_order_with_whole_capture_selection_and_no_source_writes(self):
        groups = self.groups()
        files = {path for group in groups for name in group
                 for path in Path(name).parent.iterdir() if path.is_file()}
        before = {path: path.read_bytes() for path in files}
        expected = prepare_history_collection(days=[
            package('2026-11-20', 'pse_pk5_delivery_2026-11-20_second'),
            package('2026-11-21', 'pse_pk5_delivery_2026-11-21_third'),
        ])
        for order in (groups, list(reversed(groups))):
            for output_format in ('csv', 'json'):
                with self.subTest(order=order, output_format=output_format):
                    result = self.run_cli(order, output_format)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, '')
                    if output_format == 'json':
                        report = json.loads(result.stdout)
                        rows = report['rows']
                        self.assertEqual(report['periods'], 48)
                        self.assertEqual(report['history_audit'], expected['history_audit'])
                        self.assertEqual([day['delivery_date'] for day in report['days']],
                                         ['2026-11-20', '2026-11-21'])
                        self.assertEqual(len(report['days'][0]['pk5_captures']), 2)
                        self.assertEqual(len(report['days'][0]['rejected_captures']), 1)
                        self.assertEqual(report['days'][0]['rejected_captures'][0]['reason'],
                                         'SUPERSEDED_JWCD_PUBLICATIONS')
                    else:
                        rows = list(csv.DictReader(io.StringIO(result.stdout)))
                    self.assertEqual(rows, list(expected['rows']))
        self.assertEqual({path: path.read_bytes() for path in files}, before)

    def test_invalid_later_package_leaves_no_partial_csv_or_json_in_either_order(self):
        first, second = self.groups()
        metadata_path = Path(second[1])
        metadata = json.loads(metadata_path.read_text())
        for field, value, error in (
            ('raw_sha256', '0' * 64, 'SHA-256'),
            ('snapshot_id', 'invalid', 'snapshot_id mismatch'),
            ('retrieved_at_utc', '2026-11-20T08:50:00.000001Z',
             'RETRIEVAL_STARTED_AFTER_CUTOFF'),
        ):
            with self.subTest(field=field), TemporaryDirectory() as temporary:
                target = Path(temporary)
                (target / metadata['raw_file']).write_bytes(
                    (metadata_path.parent / metadata['raw_file']).read_bytes())
                invalid = target / 'capture.json'
                changed = {**metadata, field: value}
                if field == 'retrieved_at_utc':
                    changed['retrieval_completed_at_utc'] = '2026-11-20T08:50:00.000002Z'
                invalid.write_text(json.dumps(changed))
                broken = [second[0], str(invalid)]
                for order in ([first, broken], [broken, first]):
                    for output_format in ('csv', 'json'):
                        result = self.run_cli(order, output_format)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertEqual(result.stdout, '')
                        self.assertIn(error, result.stderr)

    def test_duplicate_dates_and_missing_capture_are_rejected_without_output(self):
        first, _ = self.groups()
        for groups, error in (([first, first], 'Duplicate delivery date'),
                              ([[first[0]]], 'at least one PK5 capture')):
            for output_format in ('csv', 'json'):
                result = self.run_cli(groups, output_format)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn(error, result.stderr)
