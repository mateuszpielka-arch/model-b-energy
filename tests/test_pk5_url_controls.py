"""PK5 source provenance must not rely on urlsplit stripping control characters."""
from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_source_history import load_pk5_capture, load_pk5_snapshot

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-28_seventh'
DAY = date(2026, 11, 28)
ERROR = 'Unexpected PK5 source URL or delivery filter'


class PK5URLControlsTests(unittest.TestCase):
    def test_control_characters_and_outer_whitespace_are_not_silently_normalized(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        url = original['url']
        # urlsplit strips leading C0/space and removes CR/LF/TAB within a URL.
        values = (
            tuple(chr(code) + url for code in range(33))
            + tuple(url + char for char in (' ', '\t', '\n', '\r', '\x00', '\x7f', '\u00a0'))
            + tuple(url[:index] + char + url[index:]
                    for index in (10, url.index('/api/'), url.index('?') + 3)
                    for char in ('\t', '\r', '\n'))
        )
        self.assertEqual(len(values), 49)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            for value in values:
                path.write_text(json.dumps({**original, 'url': value, 'response_url': value}))
                before = path.read_bytes()
                for loader in (load_pk5_capture, load_pk5_snapshot):
                    with self.subTest(value=value, loader=loader.__name__), patch(
                        'shadow_ingest_manifest._inside',
                        side_effect=AssertionError('Unexpected evidence path access')
                    ) as inside:
                        with self.assertRaisesRegex(ValueError, ERROR):
                            loader(path, day=DAY)
                        inside.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                with patch('shadow_pk5_snapshot.json.loads') as parse:
                    with self.assertRaisesRegex(ValueError, ERROR):
                        prepare_pk5_snapshot(
                            raw=b'not JSON', day=DAY.isoformat(), source_url=value,
                            retrieved_at=datetime.fromisoformat(original['retrieved_at_utc']),
                            retrieval_completed_at=datetime.fromisoformat(
                                original['retrieval_completed_at_utc']))
                    parse.assert_not_called()

    def test_equivalent_url_encoding_keeps_original_values_identity_and_bytes(self):
        files = {p: p.read_bytes() for p in DIRECTORY.iterdir() if p.is_file()}
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        baseline = load_pk5_snapshot(DIRECTORY / 'capture.json', day=DAY)
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / original['raw_file']).write_bytes(files[DIRECTORY / original['raw_file']])
            path = output / 'capture.json'
            for url in (original['url'], original['url'].replace('+', '%20').replace('%24filter', '$filter')):
                path.write_text(json.dumps({**original, 'url': url, 'response_url': url}))
                capture = load_pk5_capture(path, day=DAY)
                self.assertEqual(capture['source_url'], url)
                snapshot = load_pk5_snapshot(path, day=DAY)
                self.assertEqual({**snapshot, 'source_url': baseline['source_url']}, baseline)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_cli_invalid_source_never_emits_partial_json_in_either_input_order(self):
        original = json.loads((DIRECTORY / 'capture.json').read_text())
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'capture.json'
            url = original['url'].replace('api.raporty', 'api.\nraporty')
            path.write_text(json.dumps({**original, 'url': url, 'response_url': url}))
            before = path.read_bytes()
            # No gzip is staged: source validation must take precedence over missing evidence.
            for ordered in ((DIRECTORY / 'capture.json', path), (path, DIRECTORY / 'capture.json')):
                result = subprocess.run(
                    [sys.executable, str(ROOT / 'shadow_pk5_audit.py'), '--day', DAY.isoformat(),
                     '--require-selection', *map(str, ordered)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn(ERROR, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
