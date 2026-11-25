"""Paired coverage is an explicit scoped CLI gate, independent of model admission."""
import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures'
PK5 = [('2026-11-25', FIXTURE / 'pse_pk5_delivery_2026-11-25_eighth/capture.json'),
       ('2026-11-26', FIXTURE / 'pse_pk5_delivery_2026-11-26_twelfth/capture.json')]
MANIFESTS = [FIXTURE / ('tge_delivery_2026-11-' + day) / 'manifest.json'
             for day in ('24', '25', '26')]


def command(manifests, *flags, start='2026-11-24', end='2026-11-27', reverse=False):
    args = [sys.executable, str(ROOT / 'shadow_source_coverage.py'),
            '--start-day', start, '--end-day', end]
    for day, path in (list(reversed(PK5)) if reverse else PK5):
        args += ['--source-day', day, str(path)]
    for path in manifests:
        args += ['--fix1-manifest', str(path)]
    return subprocess.run(args + list(flags), capture_output=True, text=True)


def fingerprints():
    paths = {p for source in [*[p for _, p in PK5], *MANIFESTS]
             for p in source.parent.iterdir() if p.is_file()}
    return {p: sha256(p.read_bytes()).hexdigest() for p in paths}


class PairedCoverageCLITests(unittest.TestCase):
    def test_all_three_source_gap_types_fail_after_complete_json_and_complete_scope_passes(self):
        before = fingerprints()
        for reverse in (False, True):
            manifests = list(reversed(MANIFESTS[:2])) if reverse else MANIFESTS[:2]
            normal = command(manifests, '--require-selection', '--fail-on-conflict', reverse=reverse)
            strict = command(manifests, '--require-selection', '--fail-on-conflict',
                             '--require-paired-coverage', reverse=reverse)
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertEqual(strict.returncode, 3, strict.stderr)
            self.assertEqual(strict.stderr, '')
            self.assertEqual(strict.stdout, normal.stdout)
            report = json.loads(strict.stdout)
            self.assertFalse(report['model_readiness_assessed'])
            coverage = report['source_coverage']
            self.assertEqual(coverage['both_source_days'], ['2026-11-25'])
            self.assertEqual(coverage['fix1_only_days'], ['2026-11-24'])
            self.assertEqual(coverage['pk5_only_days'], ['2026-11-26'])
            self.assertEqual(coverage['neither_source_days'], ['2026-11-27'])
            for gap_day in ('2026-11-24', '2026-11-26', '2026-11-27'):
                gap = command(manifests, '--require-paired-coverage',
                              start=gap_day, end=gap_day, reverse=reverse)
                self.assertEqual(gap.returncode, 3, gap.stderr)
                self.assertEqual(gap.stderr, '')
                self.assertEqual(json.loads(gap.stdout)['source_coverage']['both_source_days'], [])
            positive = command(manifests, '--require-paired-coverage', '--require-selection',
                               '--fail-on-conflict', start='2026-11-25', end='2026-11-25',
                               reverse=reverse)
            baseline = command(manifests, start='2026-11-25', end='2026-11-25', reverse=reverse)
            self.assertEqual(positive.returncode, 0, positive.stderr)
            self.assertEqual(positive.stderr, '')
            self.assertEqual(positive.stdout, baseline.stdout)
            self.assertFalse(json.loads(positive.stdout)['model_readiness_assessed'])
        self.assertEqual(fingerprints(), before)

    def test_price_conflict_blocks_paired_scope_but_global_conflict_flag_remains_independent(self):
        before = fingerprints()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'synthetic-conflict'
            shutil.copytree(MANIFESTS[1].parent, directory)
            path = directory / 'manifest.json'
            manifest = json.loads(path.read_text())
            entry, = manifest['entries']
            raw_path = directory / entry['raw_file']
            reference_path = directory / entry['reference_file']
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
            path.write_text(json.dumps(manifest))
            copies = {p: p.read_bytes() for p in directory.iterdir()}
            for reverse in (False, True):
                manifests = [MANIFESTS[1], path, MANIFESTS[2]]
                if reverse:
                    manifests.reverse()
                for scope, code in (('2026-11-25', 3), ('2026-11-26', 0)):
                    normal = command(manifests, start=scope, end=scope, reverse=reverse)
                    strict = command(manifests, '--require-paired-coverage',
                                     start=scope, end=scope, reverse=reverse)
                    self.assertEqual(strict.returncode, code, strict.stderr)
                    self.assertEqual(strict.stderr, '')
                    self.assertEqual(strict.stdout, normal.stdout)
                    report = json.loads(strict.stdout)
                    self.assertEqual(report['source_coverage']['both_source_days'], [scope])
                    self.assertEqual(report['fix1_audit']['days_with_price_conflicts'], 1)
                    self.assertEqual(report['source_coverage']['fix1_price_conflict_days'],
                                     ['2026-11-25'] if code else [])
                    global_strict = command(manifests, '--require-paired-coverage', '--fail-on-conflict',
                                            start=scope, end=scope, reverse=reverse)
                    self.assertEqual(global_strict.returncode, 3, global_strict.stderr)
                    self.assertEqual(global_strict.stdout, normal.stdout)
            self.assertEqual({p: p.read_bytes() for p in copies}, copies)
        self.assertEqual(fingerprints(), before)

    def test_invalid_inputs_take_precedence_over_gap_exit_and_emit_no_partial_json(self):
        absent = FIXTURE / 'absent-manifest.json'
        for manifests in ([MANIFESTS[1], absent], [absent, MANIFESTS[1]]):
            result = command(manifests, '--require-paired-coverage', '--require-selection',
                             '--fail-on-conflict')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
            self.assertIn('error:', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        result = command([MANIFESTS[1]], '--require-paired-coverage',
                         start='2026-11-27', end='2026-11-24')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
