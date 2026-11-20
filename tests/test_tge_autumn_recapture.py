import csv
from datetime import datetime
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest

from tge_parser_safe import parse_complete_standard_day, parse_fix1
from tge_period_labels import parse_fix1_labels
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures'
DIRECTORY = FIXTURES / 'tge_delivery_2026-10-25_recaptured'


class AutumnRecaptureTests(unittest.TestCase):
    def test_real_recapture_preserves_all_25_prices_against_previous_source_and_xpath(self):
        metadata = json.loads((DIRECTORY / 'capture.json').read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        reference = (DIRECTORY / metadata['reference_file']).read_bytes()
        self.assertEqual((metadata['http_status'], metadata['hours'], metadata['bytes']),
                         (200, 25, len(raw)))
        self.assertEqual(metadata['response_url'], metadata['url'])
        self.assertEqual(metadata['url'],
                         'https://tge.pl/energia-elektryczna-rdn?dateShow=24-10-2026')
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(sha256(reference).hexdigest(), metadata['reference_sha256'])
        original = (FIXTURES / 'tge_rdn_session_2026-10-24_captured_2026-11-02.html').read_bytes()
        self.assertNotEqual(sha256(raw).hexdigest(), sha256(original).hexdigest())
        rows = parse_fix1_labels(raw.decode(), '2026-10-25')
        self.assertEqual(rows, parse_fix1_labels(original.decode(), '2026-10-25'))
        self.assertEqual(rows, tuple(extract_reference(raw, '2026-10-25', autumn_25h=True)))
        csv_rows = list(csv.DictReader(reference.decode().splitlines()))
        self.assertEqual(rows, tuple((row['tge_hour_label'], Decimal(row['price_pln_mwh']))
                                     for row in csv_rows))
        self.assertEqual({row['delivery_date'] for row in csv_rows}, {'2026-10-25'})
        self.assertEqual([label for label, _ in rows],
                         ['H01', 'H02', 'H02a'] + [f'H{hour:02d}' for hour in range(3, 25)])
        self.assertEqual(dict(rows)['H02a'], Decimal('380.01'))
        self.assertEqual(dict(rows)['H10'], Decimal('-13.46'))
        self.assertEqual(rows[-1], ('H24', Decimal('370.01')))

    def test_recapture_does_not_silently_become_standard_day_or_utc_history(self):
        metadata = json.loads((DIRECTORY / 'capture.json').read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        reference = (DIRECTORY / metadata['reference_file']).read_text()
        self.assertEqual(metadata['period_basis'], 'TGE_LABEL_ONLY')
        self.assertNotIn('period_start_utc', reference.splitlines()[0])
        self.assertFalse((DIRECTORY / 'manifest.json').exists())
        started = datetime.fromisoformat(metadata['retrieved_at_utc'])
        completed = datetime.fromisoformat(metadata['retrieval_completed_at_utc'])
        self.assertLessEqual(started, completed)
        self.assertEqual(completed.date().isoformat(), '2026-11-20')
        self.assertGreater(completed.date().isoformat(), metadata['delivery_date'])
        with self.assertRaisesRegex(ValueError, 'Unsupported hourly label'):
            parse_fix1(raw.decode(), metadata['delivery_date'])
        with self.assertRaisesRegex(ValueError, 'DST'):
            parse_complete_standard_day(raw.decode(), metadata['delivery_date'])
        with self.assertRaisesRegex(ValueError, 'Unexpected hourly label'):
            extract_reference(raw, metadata['delivery_date'])
