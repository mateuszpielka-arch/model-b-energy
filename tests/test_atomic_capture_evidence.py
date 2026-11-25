"""Evidence publication is complete and exclusive, including competing writers."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from tools.capture_tge_validation import _write_new_evidence


class AtomicEvidenceTests(unittest.TestCase):
    def test_complete_closed_and_synced_payload_is_published_once_without_overwrite(self):
        payload = bytes(range(256)) * 4096
        events = []
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'evidence.json.gz'
            def synced(fd):
                os.fsync(fd)
                events.append('fsync')
            def publish(source, destination):
                self.assertEqual(events, ['fsync'])
                self.assertFalse(target.exists())
                self.assertEqual(Path(source).read_bytes(), payload)
                os.link(source, destination)
                events.append('link')
            with patch('tools.capture_tge_validation.fsync', side_effect=synced), patch(
                'tools.capture_tge_validation.link', side_effect=publish
            ):
                _write_new_evidence(target, payload)
            self.assertEqual(events, ['fsync', 'link'])
            self.assertEqual(target.read_bytes(), payload)
            for replacement in (payload, b'different bytes'):
                with self.assertRaises(FileExistsError):
                    _write_new_evidence(target, replacement)
                self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failure_before_publication_cleans_staging_and_allows_retry(self):
        for stage in ('fsync', 'link'):
            with self.subTest(stage=stage), TemporaryDirectory() as temporary:
                target = Path(temporary) / 'capture.json'
                with patch('tools.capture_tge_validation.' + stage,
                           side_effect=OSError('injected ' + stage + ' failure')):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        _write_new_evidence(target, b'complete metadata')
                self.assertFalse(target.exists())
                self.assertEqual(list(target.parent.iterdir()), [])
                _write_new_evidence(target, b'complete metadata')
                self.assertEqual(target.read_bytes(), b'complete metadata')
                self.assertEqual(list(target.parent.iterdir()), [target])

    def test_eight_competing_writers_publish_one_whole_payload_and_leave_no_staging(self):
        barrier = Barrier(8, timeout=10)
        payloads = [bytes([index]) * (65536 + index) for index in range(8)]
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'source.html'
            def publish(source, destination):
                self.assertIn(Path(source).read_bytes(), payloads)
                barrier.wait()
                os.link(source, destination)
            def write(payload):
                try:
                    _write_new_evidence(target, payload)
                    return 'INSERT', payload
                except FileExistsError:
                    return 'EXISTS', payload
            with patch('tools.capture_tge_validation.link', side_effect=publish), ThreadPoolExecutor(
                max_workers=8
            ) as pool:
                results = list(pool.map(write, payloads))
            self.assertEqual([status for status, _ in results].count('INSERT'), 1)
            self.assertEqual([status for status, _ in results].count('EXISTS'), 7)
            winner, = [payload for status, payload in results if status == 'INSERT']
            self.assertEqual(target.read_bytes(), winner)
            self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == '__main__':
    unittest.main()
