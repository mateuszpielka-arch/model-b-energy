"""Strict conflict exit preserves complete diagnostics and never rewrites evidence."""
from hashlib import sha256
import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures'
MANIFEST = FIXTURE / 'tge_delivery_2026-11-25/manifest.json'
PK5 = FIXTURE / 'pse_pk5_delivery_2026-11-25_eighth/capture.json'


def command(manifests, *flags, scope='2026-11-25'):
    result = [sys.executable, str(ROOT / 'shadow_source_coverage.py'),
              '--source-day', '2026-11-25', str(PK5),
              '--start-day', scope, '--end-day', scope]
    for manifest in manifests:
        result += ['--fix1-manifest', str(manifest)]
    return subprocess.run(result + list(flags), capture_output=True, text=True)


class JointConflictCLITests(unittest.TestCase):
    def test_consistent_real_source_keeps_success_and_identical_json_in_strict_mode(self):
        baseline = command([MANIFEST])
        strict = command([MANIFEST], '--fail-on-conflict', '--require-selection')
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(strict.returncode, 0, strict.stderr)
        self.assertEqual(strict.stderr, '')
        self.assertEqual(json.loads(strict.stdout), json.loads(baseline.stdout))
        self.assertEqual(json.loads(strict.stdout)['fix1_audit']['days_with_price_conflicts'], 0)

    def test_temporary_price_conflict_fails_even_outside_scope_and_invalid_input_takes_precedence(self):
        originals = {p: p.read_bytes() for directory in (MANIFEST.parent, PK5.parent)
                     for p in directory.iterdir() if p.is_file()}
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'synthetic-conflict'
            shutil.copytree(MANIFEST.parent, target)
            manifest = target / 'manifest.json'
            metadata = json.loads(manifest.read_text())
            entry, = metadata['entries']
            raw_path, reference_path = target / entry['raw_file'], target / entry['reference_file']
            raw = gzip.decompress(raw_path.read_bytes())
            reference = reference_path.read_bytes()
            self.assertEqual(raw.count(b'944,60'), 1)
            self.assertEqual(reference.count(b'944.60'), 1)
            raw = raw.replace(b'944,60', b'945,60', 1)
            reference = reference.replace(b'944.60', b'945.60', 1)
            raw_path.write_bytes(gzip.compress(raw, mtime=0))
            reference_path.write_bytes(reference)
            entry.update(raw_sha256=sha256(raw).hexdigest(),
                         reference_sha256=sha256(reference).hexdigest(), bytes=len(raw))
            manifest.write_text(json.dumps(metadata))
            before = {p: p.read_bytes() for p in target.iterdir()}
            for scope in ('2026-11-25', '2026-11-26'):
                for paths in ([MANIFEST, manifest], [manifest, MANIFEST]):
                    baseline = command(paths, scope=scope)
                    self.assertEqual(baseline.returncode, 0, baseline.stderr)
                    report = json.loads(baseline.stdout)
                    self.assertEqual(report['fix1_audit']['days_with_price_conflicts'], 1)
                    day, = report['fix1_audit']['days']
                    self.assertEqual(day['conflicting_periods_utc'], ['2026-11-24T23:00:00+00:00'])
                    self.assertEqual([p['price_pln_mwh'] for p in day['price_conflicts'][0]['prices']],
                                     ['944.60', '945.60'])
                    self.assertEqual(report['source_coverage']['fix1_price_conflict_days'],
                                     ['2026-11-25'] if scope == '2026-11-25' else [])
                    for flags in (('--fail-on-conflict',),
                                  ('--fail-on-conflict', '--require-selection')):
                        strict = command(paths, *flags, scope=scope)
                        self.assertEqual(strict.returncode, 3, strict.stderr)
                        self.assertEqual(strict.stderr, '')
                        self.assertEqual(json.loads(strict.stdout), report)
            # A complete conflict report is not allowed to hide invalid evidence.
            absent = target / 'absent.json'
            for paths in ([MANIFEST, manifest, absent], [absent, manifest, MANIFEST]):
                invalid = command(paths, '--fail-on-conflict')
                self.assertEqual(invalid.returncode, 2)
                self.assertEqual(invalid.stdout, '')
                self.assertIn('error:', invalid.stderr)
                self.assertNotIn('Traceback', invalid.stderr)
            self.assertEqual({p: p.read_bytes() for p in target.iterdir()}, before)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)


if __name__ == '__main__':
    unittest.main()
