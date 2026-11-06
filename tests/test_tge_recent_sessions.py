import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest

from shadow_archive_batch import prepare_batch
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import extract_reference


FIXTURES = Path(__file__).parent / 'fixtures/tge_history_2026-11-04_2026-11-06'


class RecentOfficialSessionsTests(unittest.TestCase):
    def test_captured_fixing_i_prices_pass_parser_and_archive_admission(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        self.assertEqual(manifest['errors'], [])
        days = ['2026-11-04', '2026-11-05', '2026-11-06']
        self.assertEqual([entry['delivery_date'] for entry in manifest['entries']], days)
        for entry in manifest['entries']:
            day = entry['delivery_date']
            with self.subTest(delivery_date=day):
                session = date.fromisoformat(day) - timedelta(days=1)
                self.assertEqual(entry['session_date'], session.isoformat())
                self.assertEqual(entry['http_status'], 200)
                raw = gzip.decompress((FIXTURES / entry['raw_file']).read_bytes())
                reference = (FIXTURES / entry['reference_file']).read_bytes()
                self.assertEqual(len(raw), entry['bytes'])
                self.assertEqual(sha256(raw).hexdigest(), entry['raw_sha256'])
                self.assertEqual(sha256(reference).hexdigest(), entry['reference_sha256'])

                rows = list(csv.DictReader(reference.decode('utf-8').splitlines()))
                self.assertEqual([row['delivery_date'] for row in rows], [day] * 24)
                expected = [(row['hour'], Decimal(row['price_pln_mwh'])) for row in rows]
                self.assertEqual(extract_reference(raw, day), expected)
                actual = parse_complete_standard_day(raw.decode('utf-8'), day)
                self.assertEqual(
                    [(f'H{hour:02d}', Decimal(str(price))) for hour, price in sorted(actual.items())],
                    expected,
                )
                batch = prepare_batch(
                    raw=raw,
                    day=day,
                    source_url=entry['url'],
                    retrieved_at=datetime.fromisoformat(entry['retrieved_at_utc']),
                    reference_rows=expected,
                    reference_sha256=entry['reference_sha256'],
                )
                self.assertEqual(len(batch.rows), 24)
                self.assertEqual(batch.raw_sha256, entry['raw_sha256'])
