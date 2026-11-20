from datetime import date
import gzip
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from lxml import html
from tge_period_labels import parse_fix1_labels
from tools.capture_tge_validation import _fetch_session, capture, extract_reference


DIRECTORY = Path(__file__).parent / 'fixtures/tge_delivery_2026-03-29_redirected'


class SpringRedirectTests(unittest.TestCase):
    def test_real_redirect_is_not_a_delivery_price_source_and_is_rejected_before_body_read(self):
        metadata = json.loads((DIRECTORY / 'capture.json').read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        self.assertEqual(len(raw), metadata['bytes'])
        self.assertEqual(sha256(raw).hexdigest(), metadata['raw_sha256'])
        self.assertEqual(metadata['http_status'], 200)
        self.assertEqual(metadata['url'],
                         'https://tge.pl/energia-elektryczna-rdn?dateShow=28-03-2026')
        self.assertEqual(metadata['response_url'], 'https://tge.pl/')
        self.assertEqual(html.fromstring(raw).xpath('//table[@id="rdn"]'), [])
        self.assertEqual(metadata['observed_rdn_tables'], 0)
        with self.assertRaisesRegex(ValueError, 'Expected one official RDN table'):
            extract_reference(raw, metadata['delivery_date'])
        # Replay the observed final URL/status. Source gate must run before read().
        with patch('tools.capture_tge_validation.urlopen') as request:
            response = request.return_value.__enter__.return_value
            response.status = metadata['http_status']
            response.geturl.return_value = metadata['response_url']
            response.read.side_effect = AssertionError('Redirected body must not be consumed')
            with self.assertRaisesRegex(ValueError, 'response URL differs'):
                _fetch_session(metadata['url'], date.fromisoformat(metadata['delivery_date']))
            response.read.assert_not_called()
            self.assertEqual(request.call_count, 1)

    def test_spring_mapping_stays_blocked_and_capture_cannot_write_partial_package(self):
        metadata = json.loads((DIRECTORY / 'capture.json').read_text())
        raw = gzip.decompress((DIRECTORY / metadata['raw_file']).read_bytes())
        with self.assertRaisesRegex(ValueError, 'Spring TGE label convention not yet verified'):
            parse_fix1_labels(raw.decode(), metadata['delivery_date'])
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_tge_validation._fetch_session',
            side_effect=AssertionError('Unsupported spring capture must not fetch'),
        ) as fetch:
            target = Path(temporary)
            with self.assertRaisesRegex(ValueError, '24-hour Warsaw day'):
                capture(date.fromisoformat(metadata['delivery_date']), target)
            self.assertEqual(list(target.iterdir()), [])
            fetch.assert_not_called()
        self.assertFalse((DIRECTORY / 'manifest.json').exists())
        self.assertEqual(list(DIRECTORY.glob('*.csv')), [])
