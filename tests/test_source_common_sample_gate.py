"""Strict common-sample exit distinguishes diagnostics from a model comparison."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'tests/fixtures/tge_delivery_2026-11-25/manifest.json'
PK5 = ROOT / 'tests/fixtures/pse_pk5_delivery_2026-11-25_eighth/capture.json'
STAMP = '2026-11-24T10:15:51.127696+00:00'


def command(*extra, manifest=MANIFEST, evaluate=True, output_format='json'):
    args = [sys.executable, str(ROOT / 'shadow_source_collection.py'),
            '--day', str(manifest), str(PK5), '--format', output_format]
    if evaluate:
        args += ['--walk-forward-at', STAMP]
    return subprocess.run(args + list(extra), capture_output=True, text=True)


class CommonSampleGateTests(unittest.TestCase):
    def test_real_source_diagnostics_exit_three_for_incomplete_or_absent_target(self):
        files = {p: p.read_bytes() for directory in (MANIFEST.parent, PK5.parent)
                 for p in directory.iterdir() if p.is_file()}
        for target in ('2026-11-25', '2026-11-26'):
            flags = ['--target-from', target, '--target-to', target]
            baseline = command(*flags)
            strict = command(*flags, '--require-common-sample')
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            self.assertEqual(strict.returncode, 3, strict.stderr)
            self.assertEqual(strict.stderr, '')
            self.assertEqual(json.loads(strict.stdout), json.loads(baseline.stdout))
            report = json.loads(strict.stdout)
            self.assertEqual(report['periods'], 24)
            comparison = report['evaluation']['walkforward']
            self.assertEqual((comparison['common_days'], comparison['common_periods']), (0, 0))
            self.assertEqual(comparison['ranking'], [])
            if target == '2026-11-25':
                self.assertEqual(len(comparison['skipped']), 1)
                self.assertEqual(comparison['target_coverage']['admitted_but_unscored_days'], 1)
            else:
                self.assertEqual(comparison['skipped'], [])
                self.assertEqual(comparison['target_coverage']['absent_from_csv_days'], 1)
        self.assertEqual({p: p.read_bytes() for p in files}, files)

    def test_diagnostic_file_is_complete_at_exit_three_and_is_never_overwritten(self):
        baseline = command()
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'diagnostic.json'
            saved = command('--require-common-sample', '--output', str(target))
            self.assertEqual(saved.returncode, 3, saved.stderr)
            self.assertEqual((saved.stdout, saved.stderr), ('', ''))
            self.assertEqual(target.read_bytes(), baseline.stdout.encode())
            before = target.read_bytes()
            again = command('--require-common-sample', '--output', str(target))
            self.assertEqual(again.returncode, 2)
            self.assertEqual(again.stdout, '')
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(Path(temporary).iterdir()), [target])

    def test_invalid_options_or_sources_exit_two_without_any_output_file(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'must-not-exist.json'
            for options in (
                dict(evaluate=False),
                dict(output_format='csv'),
                dict(manifest=Path(temporary) / 'missing.json'),
            ):
                with self.subTest(options=options):
                    result = command('--require-common-sample', '--output', str(target), **options)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('error:', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
