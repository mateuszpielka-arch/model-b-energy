"""Reject unsupported target bounds before evidence I/O or publication."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shadow_source_collection import main

ROOT = Path(__file__).resolve().parents[1]


def arguments(*extra):
    return ['shadow_source_collection.py', '--day', 'absent-manifest', 'absent-capture',
            '--format', 'json', '--walk-forward-at', '2026-11-24T10:15:51+00:00', *extra]


class SourceTargetBoundsTests(unittest.TestCase):
    def test_extreme_bounds_never_read_sources_evaluate_or_publish(self):
        for option in ('--target-from', '--target-to'):
            for bound in ('0001-01-01', '9999-12-31'):
                stdout, stderr = io.StringIO(), io.StringIO()
                with self.subTest(option=option, bound=bound), patch(
                    'sys.argv', arguments(option, bound, '--output', 'absent-output.json'),
                ), patch('shadow_source_collection.load_history_collection') as loader, patch(
                    'shadow_source_collection.evaluate_csv_rows',
                ) as evaluate, patch(
                    'shadow_source_collection.write_output_exclusively',
                ) as publish, redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        main()
                    self.assertEqual(caught.exception.code, 2)
                    loader.assert_not_called()
                    evaluate.assert_not_called()
                    publish.assert_not_called()
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn('Supported target date bounds required', stderr.getvalue())

    def test_cli_extreme_bounds_have_precedence_over_missing_sources_and_keep_files(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'export.json'
            for initial in (None, b'previous immutable export\n'):
                if initial is not None:
                    target.write_bytes(initial)
                for option in ('--target-from', '--target-to'):
                    for bound in ('0001-01-01', '9999-12-31'):
                        result = subprocess.run(
                            [sys.executable, str(ROOT / 'shadow_source_collection.py'),
                             *arguments(option, bound, '--output', str(target))[1:]],
                            capture_output=True, text=True)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertEqual(result.stdout, '')
                        self.assertIn('Supported target date bounds required', result.stderr)
                        self.assertNotIn('Traceback', result.stderr)
                        if initial is None:
                            self.assertEqual(list(Path(temporary).iterdir()), [])
                        else:
                            self.assertEqual(target.read_bytes(), initial)
                            self.assertEqual(list(Path(temporary).iterdir()), [target])

    def test_adjacent_supported_dates_reach_existing_loader(self):
        for option, bound in (('--target-from', '0001-01-02'), ('--target-to', '9999-12-30')):
            stderr = io.StringIO()
            with self.subTest(option=option), patch('sys.argv', arguments(option, bound)), patch(
                'shadow_source_collection.load_history_collection',
                side_effect=ValueError('Source validation sentinel'),
            ) as loader, redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)
            loader.assert_called_once()
            self.assertIn('Source validation sentinel', stderr.getvalue())
            self.assertNotIn('Supported target date bounds required', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
