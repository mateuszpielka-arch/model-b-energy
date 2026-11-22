"""Input failures must not expose partial walk-forward reports."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_history_audit import read_csv
from shadow_walkforward_csv import evaluate_csv_rows


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/tge_delivery_2026-11-22/tge_delivery_2026-11-22.csv'
STAMP = '2026-11-22T20:00:00+00:00'


class WalkforwardCliInputErrorTests(unittest.TestCase):
    def command(self, paths):
        return [sys.executable, str(ROOT / 'shadow_walkforward_csv.py'),
                '--evaluated-at', STAMP, *map(str, paths)]

    def test_invalid_later_or_first_csv_returns_two_without_report_or_source_changes(self):
        good_hash = sha256(FIXTURE.read_bytes()).hexdigest()
        cases = (
            ('duplicate.csv', b'delivery_date,delivery_date\n2026-11-22,2026-11-22\n'),
            ('fields.csv', b'delivery_date,hour\n2026-11-22,H01,extra\n'),
            ('quotes.csv', b'delivery_date,hour\n"2026-11-22,H01\n'),
            ('encoding.csv', b'delivery_date,hour\n2026-11-22,\xff\n'),
            ('missing.csv', None),
        )
        with TemporaryDirectory() as directory:
            for filename, content in cases:
                path = Path(directory) / filename
                if content is not None:
                    path.write_bytes(content)
                for paths in ([FIXTURE, path], [path, FIXTURE]):
                    with self.subTest(filename=filename, order=paths):
                        completed = subprocess.run(self.command(paths), capture_output=True, text=True)
                        self.assertEqual(completed.returncode, 2, completed.stderr)
                        self.assertEqual(completed.stdout, '')
                        self.assertIn('error:', completed.stderr)
                        self.assertNotIn('Traceback', completed.stderr)
                        if content is None:
                            self.assertFalse(path.exists())
                        else:
                            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(sha256(FIXTURE.read_bytes()).hexdigest(), good_hash)

    def test_valid_incomplete_provenance_remains_a_complete_diagnostic_report(self):
        before = FIXTURE.read_bytes()
        completed = subprocess.run(self.command([FIXTURE]), capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, '')
        result = json.loads(completed.stdout)
        self.assertEqual(result, evaluate_csv_rows(read_csv(FIXTURE),
                                                  evaluated_at=datetime.fromisoformat(STAMP)))
        self.assertEqual(result['csv_admission']['admitted_days'], 0)
        self.assertEqual(result['csv_admission']['delivery_days'], 1)
        self.assertEqual(result['walkforward']['ranking'], [])
        self.assertEqual(FIXTURE.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
