import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from dataclasses import replace
from hashlib import sha256
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from shadow_archive_batch import read_reference_csv, prepare_batch
from shadow_asof import cutoff_for_day
from shadow_history_audit import read_csv
from shadow_source_history import prepare_history_rows
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
