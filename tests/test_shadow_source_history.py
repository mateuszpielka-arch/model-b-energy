import csv
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from dataclasses import replace
from hashlib import sha256
import gzip
import json
import io
import subprocess
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_ingest_manifest import ingest_manifest

from shadow_archive_batch import read_reference_csv, prepare_batch
from shadow_asof import cutoff_for_day
from shadow_history_audit import read_csv
from shadow_source_history import (
    prepare_history_rows, prepare_history_rows_from_captures, prepare_history_report_from_captures,
)
from shadow_walkforward_csv import evaluate_csv_rows


FIXTURES = Path(__file__).parent / 'fixtures'


class SourceHistoryTests(unittest.TestCase):
    def inputs(self):
        actual_dir = FIXTURES / 'tge_delivery_2026-11-20'
        entry, = json.loads((actual_dir / 'manifest.json').read_text())['entries']
        raw = gzip.decompress((actual_dir / entry['raw_file']).read_bytes())
        reference = (actual_dir / entry['reference_file']).read_bytes()
        batch = prepare_batch(
            raw=raw, day=entry['delivery_date'], source_url=entry['url'],
            retrieved_at=datetime.fromisoformat(entry['retrieved_at_utc']),
            reference_sha256=entry['reference_sha256'],
            reference_rows=read_reference_csv(reference, entry['delivery_date'], require_utc=True),
        )
        pk5_dir = FIXTURES / 'pse_pk5_delivery_2026-11-20_second'
        metadata = json.loads((pk5_dir / 'capture.json').read_text())
        return dict(
            actual_batch=batch, actual_raw=raw, actual_reference=reference,
            pk5_raw=gzip.decompress((pk5_dir / metadata['raw_file']).read_bytes()),
            pk5_source_url=metadata['url'],
            retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
            retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
        )

    def candidate_inputs(self):
        inputs = self.inputs()
        actual = {key: value for key, value in inputs.items() if key.startswith('actual_')}
        captures = []
        for name in ('pse_pk5_delivery_2026-11-20', 'pse_pk5_delivery_2026-11-20_second'):
            directory = FIXTURES / name
            metadata = json.loads((directory / 'capture.json').read_text())
            captures.append(dict(
                raw=gzip.decompress((directory / metadata['raw_file']).read_bytes()),
                source_url=metadata['url'],
                retrieved_at=datetime.fromisoformat(metadata['retrieved_at_utc']),
                retrieval_completed_at=datetime.fromisoformat(metadata['retrieval_completed_at_utc']),
            ))
        return actual, captures

    def test_history_selects_whole_latest_real_publication_in_both_orders(self):
        actual, (early, latest) = self.candidate_inputs()
        expected = prepare_history_rows(**self.inputs())
        before = deepcopy((actual, early, latest))
        # A synthetic later retrieval of the older official bytes cannot roll back publication.
        recaptured_old = {
            **early, 'retrieved_at': latest['retrieved_at'] + timedelta(minutes=2),
            'retrieval_completed_at': latest['retrieval_completed_at'] + timedelta(minutes=2),
        }
        for captures in ([early, latest], [latest, early],
                         [recaptured_old, latest], [latest, recaptured_old]):
            with self.subTest(order=[item['retrieved_at'] for item in captures]):
                rows = prepare_history_rows_from_captures(pk5_captures=captures, **actual)
                self.assertEqual(rows, expected)
                self.assertEqual(len({row['jwcd_snapshot_id'] for row in rows}), 1)
                self.assertEqual({row['retrieved_at'] for row in rows},
                                 {latest['retrieved_at'].isoformat()})
        self.assertEqual((actual, early, latest), before)

    def test_history_rejects_same_publication_conflict_and_duplicate_capture(self):
        actual, (_, latest) = self.candidate_inputs()
        payload = json.loads(latest['raw'])
        payload['value'][0]['fcst_gen_unit_stor_prov'] += 1
        conflict = {
            **latest, 'raw': json.dumps(payload).encode(),
            'retrieved_at': latest['retrieved_at'] + timedelta(minutes=1),
            'retrieval_completed_at': latest['retrieval_completed_at'] + timedelta(minutes=1),
        }
        for captures, reason in (
            ([latest, conflict], 'CONFLICTING_JWCD_PUBLICATION'),
            ([conflict, latest], 'CONFLICTING_JWCD_PUBLICATION'),
            ([latest, latest], 'MISSING_OR_DUPLICATE_SNAPSHOT_ID'),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    prepare_history_rows_from_captures(pk5_captures=captures, **actual)

    def test_history_does_not_silently_discard_late_capture(self):
        actual, (early, latest) = self.candidate_inputs()
        cutoff = cutoff_for_day(date(2026, 11, 20))
        late = {**latest, 'retrieved_at': cutoff + timedelta(microseconds=1),
                'retrieval_completed_at': cutoff + timedelta(microseconds=2)}
        for captures in ([early, late], [late, early]):
            with self.subTest(order=[item['retrieved_at'] for item in captures]):
                with self.assertRaisesRegex(ValueError, 'RETRIEVAL_STARTED_AFTER_CUTOFF'):
                    prepare_history_rows_from_captures(pk5_captures=captures, **actual)

    def test_history_requires_explicit_capture_collection_and_fields(self):
        actual, (_, latest) = self.candidate_inputs()
        for captures in (None, [], latest, 'capture', [None],
                         [{**latest, 'day': '2026-11-20'}],
                         [{key: value for key, value in latest.items() if key != 'raw'}]):
            with self.subTest(captures=type(captures).__name__):
                with self.assertRaisesRegex(ValueError, 'PK5 capture'):
                    prepare_history_rows_from_captures(pk5_captures=captures, **actual)

    def test_cli_multiple_real_captures_select_latest_in_both_orders(self):
        early = FIXTURES / 'pse_pk5_delivery_2026-11-20/capture.json'
        latest = FIXTURES / 'pse_pk5_delivery_2026-11-20_second/capture.json'
        expected = list(prepare_history_rows(**self.inputs()))
        files = [path for directory in (early.parent, latest.parent,
                                       FIXTURES / 'tge_delivery_2026-11-20')
                 for path in directory.iterdir() if path.is_file()]
        before = {path: path.read_bytes() for path in files}
        for first, second in ((early, latest), (latest, early)):
            with self.subTest(first=first):
                command = self.command(first)
                command[-1:-1] = ['--pk5-capture', str(second)]
                completed = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, '')
                self.assertEqual(list(csv.DictReader(io.StringIO(completed.stdout))), expected)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_cli_multiple_captures_validate_all_without_partial_csv(self):
        directory = FIXTURES / 'pse_pk5_delivery_2026-11-20_second'
        valid = directory / 'capture.json'
        original = json.loads(valid.read_text())
        for case, error in (
            ('hash', 'SHA-256'), ('http', 'HTTP/source/row'), ('day', 'delivery differs'),
            ('late', 'RETRIEVAL_STARTED_AFTER_CUTOFF'),
            ('duplicate', 'MISSING_OR_DUPLICATE_SNAPSHOT_ID'),
            ('conflict', 'CONFLICTING_JWCD_PUBLICATION'),
        ):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                target = Path(temporary)
                raw = gzip.decompress((directory / original['raw_file']).read_bytes())
                metadata = dict(original)
                if case == 'hash':
                    metadata['raw_sha256'] = '0' * 64
                elif case == 'http':
                    metadata['http_status'] = 503
                elif case == 'day':
                    metadata['delivery_date'] = '2026-11-21'
                elif case == 'late':
                    metadata['retrieved_at_utc'] = '2026-11-19T08:50:00.000001Z'
                    metadata['retrieval_completed_at_utc'] = '2026-11-19T08:50:00.000002Z'
                elif case == 'conflict':
                    payload = json.loads(raw)
                    payload['value'][0]['fcst_gen_unit_stor_prov'] += 1
                    raw = json.dumps(payload).encode()
                    metadata.update(raw_sha256=sha256(raw).hexdigest(), bytes=len(raw))
                    metadata['retrieved_at_utc'] = '2026-11-19T08:17:00Z'
                    metadata['retrieval_completed_at_utc'] = '2026-11-19T08:17:01Z'
                (target / original['raw_file']).write_bytes(gzip.compress(raw))
                invalid = target / 'capture.json'
                invalid.write_text(json.dumps(metadata))
                for first, second in ((invalid, valid), (valid, invalid)):
                    with self.subTest(first=first):
                        command = self.command(first)
                        command[-1:-1] = ['--pk5-capture', str(second)]
                        completed = subprocess.run(command, capture_output=True, text=True)
                        self.assertEqual(completed.returncode, 2, completed.stderr)
                        self.assertEqual(completed.stdout, '')
                        self.assertIn(error, completed.stderr)

    def test_history_report_preserves_selected_and_superseded_evidence(self):
        actual, captures = self.candidate_inputs()
        before = deepcopy((actual, captures))
        report = prepare_history_report_from_captures(pk5_captures=captures, **actual)
        self.assertEqual(report, prepare_history_report_from_captures(
            pk5_captures=list(reversed(captures)), **actual))
        self.assertEqual(report['rows'], prepare_history_rows(**self.inputs()))
        self.assertEqual(report['periods'], 24)
        self.assertEqual(report['delivery_date'], '2026-11-20')
        self.assertEqual(report['cutoff_utc'], '2026-11-19T08:50:00+00:00')
        readiness = report['history_audit']
        self.assertEqual(readiness['complete_pair_days'], 1)
        self.assertEqual(readiness['model_ready_days'], 1)
        self.assertEqual(readiness['longest_model_ready_run'], 1)
        self.assertEqual(readiness['missing_days_for_first_comparison'],
                         {'30D': 30, '60D': 60, '90D': 90})
        self.assertEqual(readiness['comparison_candidates'], {'30D': [], '60D': [], '90D': []})
        day, = readiness['days']
        self.assertEqual(day['day'], '2026-11-20')
        self.assertEqual(day['expected_periods'], 24)
        self.assertEqual(day['valid_fix1_rows'], 24)
        self.assertEqual(day['valid_jwcd_rows'], 24)
        self.assertEqual(day['issues'], [])
        evidence = {item['raw_sha256']: item for item in report['pk5_captures']}
        self.assertEqual(len(evidence), 2)
        for capture in captures:
            item = evidence[sha256(capture['raw']).hexdigest()]
            self.assertEqual(item['retrieved_at_utc'], capture['retrieved_at'].isoformat())
            self.assertEqual(item['retrieval_completed_at_utc'],
                             capture['retrieval_completed_at'].isoformat())
            self.assertEqual(item['source_url'], capture['source_url'])
            self.assertEqual(item['periods'], 24)
        latest = evidence[sha256(captures[1]['raw']).hexdigest()]
        early = evidence[sha256(captures[0]['raw']).hexdigest()]
        self.assertTrue(latest['selected'])
        self.assertFalse(early['selected'])
        self.assertEqual(latest['latest_publication_ts_utc'], '2026-11-19T08:12:42.315000+00:00')
        self.assertEqual(report['selected_snapshot_id'], latest['snapshot_id'])
        self.assertEqual(report['rejected_captures'], [
            dict(snapshot_id=early['snapshot_id'], reason='SUPERSEDED_JWCD_PUBLICATIONS'),
        ])
        self.assertEqual(report['fix1_evidence']['raw_sha256'], sha256(actual['actual_raw']).hexdigest())
        self.assertEqual(report['fix1_evidence']['reference_sha256'],
                         sha256(actual['actual_reference']).hexdigest())
        self.assertEqual(report['fix1_evidence']['retrieved_at_utc'],
                         actual['actual_batch'].retrieved_at.isoformat())
        self.assertEqual(report['fix1_evidence']['actual_version'],
                         report['rows'][0]['actual_version'])
        self.assertEqual(json.loads(json.dumps(report))['rows'], list(report['rows']))
        self.assertEqual((actual, captures), before)

    def test_cli_json_reports_selection_and_returns_no_partial_report_on_conflict(self):
        early = FIXTURES / 'pse_pk5_delivery_2026-11-20/capture.json'
        latest = FIXTURES / 'pse_pk5_delivery_2026-11-20_second/capture.json'
        actual, captures = self.candidate_inputs()
        expected = prepare_history_report_from_captures(pk5_captures=captures, **actual)
        for first, second in ((early, latest), (latest, early)):
            command = self.command(first)
            command[-1:-1] = ['--pk5-capture', str(second), '--format', 'json']
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, '')
            self.assertEqual(json.loads(completed.stdout), json.loads(json.dumps(expected)))
        command = self.command(latest)
        command[-1:-1] = ['--pk5-capture', str(latest), '--format', 'json']
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, '')
        self.assertIn('MISSING_OR_DUPLICATE_SNAPSHOT_ID', completed.stderr)

    def command(self, metadata):
        return [
            sys.executable, str(Path(__file__).resolve().parents[1] / 'shadow_source_history.py'),
            '--pk5-capture', str(metadata),
            str(FIXTURES / 'tge_delivery_2026-11-20/manifest.json'),
        ]

    def test_fix1_manifest_cannot_truncate_actual_availability(self):
        source = FIXTURES / 'tge_delivery_2026-11-20'
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'actual'
            shutil.copytree(source, target)
            manifest = target / 'manifest.json'
            data = json.loads(manifest.read_text())
            entry, = data['entries']
            exact = entry['retrieved_at_utc']
            self.assertEqual(exact, '2026-11-19T10:17:54.057511+00:00')
            self.assertEqual(ingest_manifest(manifest)['periods'], 24)
            for invalid in (
                '2026-11-19T10:17:54.0575119Z',
                '2026-11-19T10:17:54.057511001+00:00',
                '2026-11-19T10:17+00:00',
            ):
                with self.subTest(timestamp=invalid):
                    entry['retrieved_at_utc'] = invalid
                    manifest.write_text(json.dumps(data))
                    with (patch('shadow_ingest_manifest._inside', side_effect=AssertionError('Evidence read before timestamp validation')) as read_evidence,
                          patch('shadow_ingest_manifest._ingest_batches') as write_batches):
                        with self.assertRaisesRegex(ValueError, 'retrieval timestamp'):
                            ingest_manifest(manifest, conn=object())
                        read_evidence.assert_not_called()
                        write_batches.assert_not_called()
            entry['retrieved_at_utc'] = exact
            manifest.write_text(json.dumps(data))
            self.assertEqual(ingest_manifest(manifest)['periods'], 24)

    def test_cli_rejects_fix1_availability_precision_without_partial_csv(self):
        source = FIXTURES / 'tge_delivery_2026-11-20'
        pk5 = FIXTURES / 'pse_pk5_delivery_2026-11-20_second/capture.json'
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'actual'
            shutil.copytree(source, target)
            manifest = target / 'manifest.json'
            data = json.loads(manifest.read_text())
            entry, = data['entries']
            entry['retrieved_at_utc'] = '2026-11-19T10:17:54.0575119Z'
            manifest.write_text(json.dumps(data))
            command = self.command(pk5)
            command[-1] = str(manifest)
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(completed.stdout, '')
            self.assertIn('retrieval timestamp', completed.stderr)

    def test_cli_capture_times_cannot_be_truncated_into_cutoff(self):
        directory = FIXTURES / 'pse_pk5_delivery_2026-11-20_second'
        original = json.loads((directory / 'capture.json').read_text())
        for field in ('retrieved_at_utc', 'retrieval_completed_at_utc'):
            for fraction, valid in (('000000', True), ('0000001', False)):
                with self.subTest(field=field, fraction=fraction), TemporaryDirectory() as temporary:
                    target = Path(temporary)
                    (target / original['raw_file']).write_bytes((directory / original['raw_file']).read_bytes())
                    metadata = dict(original)
                    stamp = '2026-11-19T08:50:00.' + fraction + '+00:00'
                    metadata[field] = stamp
                    if field == 'retrieved_at_utc':
                        metadata['retrieval_completed_at_utc'] = stamp
                    path = target / 'capture.json'
                    path.write_text(json.dumps(metadata))
                    completed = subprocess.run(self.command(path), capture_output=True, text=True)
                    if valid:
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        rows = list(csv.DictReader(io.StringIO(completed.stdout)))
                        self.assertEqual(len(rows), 24)
                    else:
                        self.assertEqual(completed.returncode, 2)
                        self.assertEqual(completed.stdout, '')
                        self.assertIn('timestamp', completed.stderr)

    def test_cli_exports_verified_real_rows_without_changing_source_files(self):
        directory = FIXTURES / 'pse_pk5_delivery_2026-11-20_second'
        metadata = directory / 'capture.json'
        files = list(directory.iterdir()) + list((FIXTURES / 'tge_delivery_2026-11-20').iterdir())
        before = {path: path.read_bytes() for path in files if path.is_file()}
        completed = subprocess.run(self.command(metadata), check=True, capture_output=True, text=True)
        self.assertEqual(completed.stderr, '')
        rows = list(csv.DictReader(io.StringIO(completed.stdout)))
        self.assertEqual(rows, list(prepare_history_rows(**self.inputs())))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_cli_invalid_capture_metadata_produces_no_partial_csv(self):
        directory = FIXTURES / 'pse_pk5_delivery_2026-11-20_second'
        original = json.loads((directory / 'capture.json').read_text())
        for case in ('hash', 'http', 'redirect', 'duplicate'):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                target = Path(temporary)
                (target / original['raw_file']).write_bytes((directory / original['raw_file']).read_bytes())
                metadata = dict(original)
                if case == 'hash':
                    metadata['raw_sha256'] = '0' * 64
                elif case == 'http':
                    metadata['http_status'] = 503
                elif case == 'redirect':
                    metadata['response_url'] = 'https://example.com/pk5'
                text = json.dumps(metadata)
                if case == 'duplicate':
                    text = text.replace('"retrieved_at_utc":',
                                        '"retrieved_at_utc": "2099-01-01T00:00:00Z", "retrieved_at_utc":', 1)
                path = target / 'capture.json'
                path.write_text(text)
                completed = subprocess.run(self.command(path), capture_output=True, text=True)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, '')
                self.assertIn('error:', completed.stderr)

    def test_real_sources_roundtrip_to_audit_but_do_not_manufacture_training(self):
        inputs = self.inputs()
        rows = prepare_history_rows(**inputs)
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[0]['period_start_utc'], '2026-11-19T23:00:00+00:00')
        self.assertEqual(rows[-1]['period_start_utc'], '2026-11-20T22:00:00+00:00')
        self.assertEqual(rows[0]['fix1_pln_mwh'], '716.72')
        source = json.loads(inputs['pk5_raw'], parse_float=Decimal)['value']
        self.assertEqual([row['jwcd_mw'] for row in rows],
                         [str(row['fcst_gen_unit_stor_prov']) for row in source])
        self.assertEqual([row['publication_ts'] for row in rows],
                         [datetime.fromisoformat(row['publication_ts_utc']).isoformat() + '+00:00'
                          for row in source])
        self.assertEqual({row['actual_available_at'] for row in rows},
                         {inputs['actual_batch'].retrieved_at.isoformat()})
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'history.csv'
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0])
                writer.writeheader()
                writer.writerows(rows)
            imported = read_csv(path)
        self.assertEqual(imported, list(rows))
        report = evaluate_csv_rows(
            imported, evaluated_at=inputs['actual_batch'].retrieved_at,
            target_from=date(2026, 11, 20), target_to=date(2026, 11, 20),
        )
        self.assertEqual(report['history_audit']['model_ready_days'], 1)
        self.assertEqual(report['csv_admission']['admitted_days'], 1)
        comparison = report['walkforward']
        self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
        self.assertEqual(comparison['ranking'], [])
        skipped, = comparison['skipped']
        self.assertEqual(skipped['stage'], 'ols30')
        self.assertEqual(skipped['missing_training_days'], [
            (date(2026, 11, 20) - timedelta(days=offset)).isoformat()
            for offset in range(30, 0, -1)
        ])
        for window in (30, 60, 90):
            self.assertEqual(skipped['training_csv_coverage_by_window'][f'{window}D']['absent_from_csv_days'], window)

    def test_original_fix1_bytes_and_reference_are_required(self):
        inputs = self.inputs()
        for field in ('actual_raw', 'actual_reference'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                prepare_history_rows(**{**inputs, field: inputs[field] + b' '})

    def test_matching_reference_hash_does_not_bypass_price_or_utc_checks(self):
        inputs = self.inputs()
        for original, changed in (
            (b'716.72', b'716.73'),
            (b'2026-11-19T23:00:00+00:00', b'2026-11-20T00:00:00+00:00'),
        ):
            with self.subTest(changed=changed):
                reference = inputs['actual_reference'].replace(original, changed, 1)
                self.assertNotEqual(reference, inputs['actual_reference'])
                batch = replace(inputs['actual_batch'], reference_sha256=sha256(reference).hexdigest())
                with self.assertRaises(ValueError):
                    prepare_history_rows(**{
                        **inputs, 'actual_batch': batch, 'actual_reference': reference,
                    })

    def test_late_pk5_cannot_be_promoted_to_history(self):
        inputs = self.inputs()
        cutoff = cutoff_for_day(date(2026, 11, 20))
        with self.assertRaisesRegex(ValueError, 'RETRIEVAL_STARTED_AFTER_CUTOFF'):
            prepare_history_rows(**{
                **inputs, 'retrieved_at': cutoff + timedelta(microseconds=1),
                'retrieval_completed_at': cutoff + timedelta(microseconds=2),
            })
