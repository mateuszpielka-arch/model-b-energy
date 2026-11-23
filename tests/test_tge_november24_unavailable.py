from datetime import date, datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lxml import html
from tge_parser_safe import parse_complete_standard_day
from tools.capture_tge_validation import capture, extract_reference


DIRECTORY = Path(__file__).parent / 'fixtures/tge_delivery_2026-11-24_unavailable'


def evidence():
    metadata = json.loads((DIRECTORY / 'capture.json').read_text())
    raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
    return metadata, raw


class November24UnavailableTests(unittest.TestCase):
    def test_real_http_200_page_has_24_unpublished_fix1_prices_and_fails_both_extractors(self):
        metadata, raw = evidence()
        self.assertEqual((metadata['http_status'], len(raw)), (200, 258957))
        self.assertEqual(sha256(raw).hexdigest(),
                         'b4afb7c156c20545cd9827c2b3912e83aaadaa0f610b034e30d8d952494de933')
        self.assertEqual(metadata['retrieved_at_utc'], '2026-11-23T09:12:11.049087+00:00')
        self.assertEqual(metadata['retrieval_completed_at_utc'], '2026-11-23T09:12:20.779548+00:00')
        self.assertLess(datetime.fromisoformat(metadata['retrieved_at_utc']),
                        datetime.fromisoformat(metadata['retrieval_completed_at_utc']))
        tree = html.fromstring(raw)
        rows = [[' '.join(cell.text_content().split()) for cell in row.xpath('./td')]
                for row in tree.xpath('//table[@id="rdn"]/tbody/tr')]
        target = [row for row in rows if row and row[0].startswith('2026-11-24_H')]
        self.assertEqual([row[0] for row in target],
                         [f'2026-11-24_H{hour:02d}' for hour in range(1, 25)])
        self.assertEqual({row[2] for row in target}, {'-'})
        with self.assertRaisesRegex(ValueError, 'price unavailable'):
            extract_reference(raw, '2026-11-24')
        with self.assertRaisesRegex(ValueError, 'Invalid Fixing I price'):
            parse_complete_standard_day(raw.decode(), '2026-11-24', exact=True)
        self.assertFalse((DIRECTORY / 'manifest.json').exists())
        self.assertEqual(list(DIRECTORY.glob('*.csv')), [])

    def test_capture_does_not_publish_raw_or_reference_as_accepted_evidence(self):
        metadata, raw = evidence()
        before = {path: path.read_bytes() for path in DIRECTORY.iterdir()}
        with TemporaryDirectory() as directory, patch(
            'tools.capture_tge_validation._fetch_session', return_value=(200, raw),
        ) as fetch:
            with self.assertRaisesRegex(ValueError, 'price unavailable'):
                capture(date(2026, 11, 24), Path(directory))
            fetch.assert_called_once_with(metadata['url'], date(2026, 11, 24))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual({path: path.read_bytes() for path in DIRECTORY.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
