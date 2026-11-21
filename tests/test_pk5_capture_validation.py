from datetime import date, datetime, timedelta
import gzip
from hashlib import sha256
from http.client import IncompleteRead
import json
import io
from urllib.error import HTTPError, URLError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch, call

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


    def test_transient_open_failures_retry_with_original_start_and_complete_saved_bytes(self):
        original, raw, response, times = self.inputs()
        body = io.BytesIO(b'transient HTTP error')
        failure = HTTPError(original['url'], 503, 'temporary', {'Retry-After': '1'}, body)
        with TemporaryDirectory() as temporary, patch(
            'tools.capture_pk5_validation.urlopen',
            side_effect=[failure, URLError(TimeoutError('connect timeout')), response],
        ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock, patch(
            'tools.capture_pk5_validation.sleep',
        ) as pause:
            start, end = times
            clock.now.side_effect = [start, start, start, start, start, end]
            result = capture(date(2026, 11, 21), Path(temporary))
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(pause.call_args_list, [call(1), call(2)])
            self.assertTrue(body.closed)
            self.assertEqual(result['retrieved_at_utc'], start.isoformat())
            self.assertEqual(result['retrieval_completed_at_utc'], end.isoformat())
            self.assertEqual(gzip.decompress((Path(temporary) / result['raw_file']).read_bytes()), raw)
            self.assertEqual(result['raw_sha256'], sha256(raw).hexdigest())

    def test_permanent_exhausted_or_long_wait_errors_leave_no_files(self):
        original, _, _, times = self.inputs()
        for errors, attempts, pauses in (
            ([HTTPError(original['url'], 400, 'bad request', {}, None)], 1, []),
            ([HTTPError(original['url'], 429, 'busy', {'Retry-After': '60'}, None)], 1, []),
            ([HTTPError(original['url'], 503, 'busy', {'Retry-After': 'invalid'}, None)], 1, []),
            ([URLError('DNS failure')], 1, []),
            ([TimeoutError('connect timeout')] * 3, 3, [call(1), call(2)]),
        ):
            with self.subTest(errors=errors), TemporaryDirectory() as temporary, patch(
                'tools.capture_pk5_validation.urlopen', side_effect=errors,
            ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock, patch(
                'tools.capture_pk5_validation.sleep',
            ) as pause:
                clock.now.return_value = times[0]
                with self.assertRaises((HTTPError, URLError, TimeoutError)):
                    capture(date(2026, 11, 21), Path(temporary))
                self.assertEqual(fetch.call_count, attempts)
                self.assertEqual(pause.call_args_list, pauses)
                self.assertEqual(list(Path(temporary).iterdir()), [])


    def test_retry_after_rejects_nonstandard_integer_spellings(self):
        original, _, _, times = self.inputs()
        for value in ('-1', '+2', '1_0', '1.0', '１２', ' '):
            with self.subTest(value=value), TemporaryDirectory() as temporary, patch(
                'tools.capture_pk5_validation.urlopen',
                side_effect=HTTPError(original['url'], 429, 'busy',
                                      {'Retry-After': value}, None),
            ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock, patch(
                'tools.capture_pk5_validation.sleep',
            ) as pause:
                clock.now.return_value = times[0]
                with self.assertRaises(HTTPError):
                    capture(date(2026, 11, 21), Path(temporary))
                self.assertEqual(fetch.call_count, 1)
                pause.assert_not_called()
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_retry_after_ascii_seconds_respects_server_delay(self):
        original, raw, response, times = self.inputs()
        for value, delay in (('0', 1), (' 2 ', 2), ('005', 5)):
            with self.subTest(value=value), TemporaryDirectory() as temporary, patch(
                'tools.capture_pk5_validation.urlopen',
                side_effect=[HTTPError(original['url'], 429, 'busy',
                                       {'Retry-After': value}, None), response],
            ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock, patch(
                'tools.capture_pk5_validation.sleep',
            ) as pause:
                start, end = times
                clock.now.side_effect = [start, start, start, end]
                result = capture(date(2026, 11, 21), Path(temporary))
                self.assertEqual(fetch.call_count, 2)
                pause.assert_called_once_with(delay)
                self.assertEqual(result['retrieved_at_utc'], start.isoformat())
                self.assertEqual(result['retrieval_completed_at_utc'], end.isoformat())
                self.assertEqual(gzip.decompress(
                    (Path(temporary) / result['raw_file']).read_bytes()), raw)

    def test_retry_cannot_wait_or_open_after_cutoff(self):
        original, _, _, _ = self.inputs()
        cutoff = cutoff_for_day(date(2026, 11, 21))
        for times, pauses in (
            ([cutoff - timedelta(microseconds=1)] * 2, []),
            ([cutoff - timedelta(seconds=3)] * 2 + [cutoff + timedelta(microseconds=1)],
             [call(1)]),
        ):
            with self.subTest(times=times), TemporaryDirectory() as temporary, patch(
                'tools.capture_pk5_validation.urlopen',
                side_effect=HTTPError(original['url'], 503, 'temporary', {}, None),
            ) as fetch, patch('tools.capture_pk5_validation.datetime') as clock, patch(
                'tools.capture_pk5_validation.sleep',
            ) as pause:
                clock.now.side_effect = times
                with self.assertRaisesRegex(ValueError, 'retry .*after D-1 09:50'):
                    capture(date(2026, 11, 21), Path(temporary))
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(pause.call_args_list, pauses)
                self.assertEqual(list(Path(temporary).iterdir()), [])

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
                else:
                    self.assertEqual(fetch.call_count, 1)

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
