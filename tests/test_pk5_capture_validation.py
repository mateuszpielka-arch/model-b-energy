from datetime import date, datetime, timedelta
import gzip
from hashlib import sha256
from http.client import IncompleteRead
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from shadow_asof import cutoff_for_day
from tools.capture_pk5_validation import MAX_PK5_BYTES, capture


FIXTURE = Path(__file__).parent / 'fixtures/pse_pk5_delivery_2026-11-21_second'


class PK5CaptureTests(unittest.TestCase):
    def inputs(self):
        metadata = json.loads((FIXTURE / 'capture.json').read_text())
        raw = gzip.decompress((FIXTURE / metadata['raw_file']).read_bytes())
        response = MagicMock()
        response.status = 200
        response.geturl.return_value = metadata['url']
        response.length = len(raw)
        response.read.return_value = raw
        response.__enter__.return_value = response
        times = [datetime.fromisoformat(metadata[field])
                 for field in ('retrieved_at_utc', 'retrieval_completed_at_utc')]
        return metadata, raw, response, times

    def test_saved_real_response_has_original_bytes_and_exact_capture_proof(self):
        original, raw, response, times = self.inputs()
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_pk5_validation.urlopen', return_value=response,
        ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock:
            clock.now.side_effect = times
            result = capture(date(2026, 11, 21), Path(temporary))
            request = fetch.call_args.args[0]
            self.assertEqual(request.full_url, original['url'])
            self.assertEqual(fetch.call_args.kwargs['timeout'], 35)
            response.read.assert_called_once_with(MAX_PK5_BYTES + 1)
            root = Path(temporary)
            self.assertEqual(gzip.decompress((root / result['raw_file']).read_bytes()), raw)
            self.assertEqual(json.loads((root / 'capture.json').read_text()), result)
            for field in ('url', 'response_url', 'http_status', 'delivery_date', 'retrieved_at_utc',
                          'retrieval_completed_at_utc', 'raw_sha256', 'bytes', 'rows'):
                self.assertEqual(result[field], original[field], field)
            self.assertEqual(result['raw_sha256'], sha256(raw).hexdigest())
            self.assertEqual(len(list(root.iterdir())), 2)
            saved = {path.name: path.read_bytes() for path in root.iterdir()}
            clock.now.side_effect = times
            with self.assertRaises(FileExistsError):
                capture(date(2026, 11, 21), root)
            self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, saved)

    def test_late_or_invalid_response_creates_no_evidence_files(self):
        for case in ('late_start', 'late_end', 'http', 'redirect', 'partial', 'json', 'size'):
            original, raw, response, times = self.inputs()
            cutoff = cutoff_for_day(date(2026, 11, 21))
            if case == 'late_start':
                times = [cutoff + timedelta(microseconds=1)]
            elif case == 'late_end':
                times[1] = cutoff + timedelta(microseconds=1)
            elif case == 'http':
                response.status = 503
            elif case == 'redirect':
                response.geturl.return_value = 'https://example.com/pk5'
            elif case == 'partial':
                response.length = len(raw) + 1
            elif case == 'json':
                response.read.return_value = b'{"value":[]}'
                response.length = len(b'{"value":[]}')
            elif case == 'size':
                response.length = MAX_PK5_BYTES + 1
            with self.subTest(case=case), TemporaryDirectory() as temporary, patch(
                'tools.capture_pk5_validation.urlopen', return_value=response,
            ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock:
                clock.now.side_effect = times
                with self.assertRaises((ValueError, IncompleteRead)):
                    capture(date(2026, 11, 21), Path(temporary))
                self.assertEqual(list(Path(temporary).iterdir()), [])
                if case == 'late_start':
                    fetch.assert_not_called()

    def test_existing_metadata_is_preserved_and_new_raw_is_rolled_back(self):
        original, _, response, times = self.inputs()
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_pk5_validation.urlopen', return_value=response,
        ), patch('tools.capture_pk5_validation.datetime') as clock:
            clock.now.side_effect = times
            root = Path(temporary)
            (root / 'capture.json').write_bytes(b'existing evidence')
            with self.assertRaises(FileExistsError):
                capture(date(2026, 11, 21), root)
            self.assertEqual((root / 'capture.json').read_bytes(), b'existing evidence')
            self.assertFalse((root / original['raw_file']).exists())
