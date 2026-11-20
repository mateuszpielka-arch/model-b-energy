import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from shadow_manifest_audit import audit_manifests


FIXTURES = Path(__file__).parent / 'fixtures'


class ManifestAuditTests(unittest.TestCase):
    def paths(self):
        return [FIXTURES / (name + '/manifest.json') for name in (
            'tge_delivery_2026-11-20', 'tge_delivery_2026-11-20_late',
            'tge_delivery_2026-11-21',
        )]

    def command(self, paths):
        return [sys.executable, str(Path(__file__).resolve().parents[1] /
                                   'shadow_manifest_audit.py'), *map(str, paths)]

    def test_real_recaptures_and_duplicate_inputs_do_not_inflate_delivery_days(self):
        paths = self.paths()
        files = [path for manifest in paths for path in manifest.parent.iterdir() if path.is_file()]
        before = {path: path.read_bytes() for path in files}
        report = audit_manifests([*paths, paths[0]])
        self.assertEqual(report, audit_manifests([paths[0], *reversed(paths)]))
        self.assertEqual((report['validated_capture_entries'], report['distinct_captures'],
                          report['duplicate_capture_entries']), (4, 3, 1))
        self.assertEqual((report['delivery_days'], report['unique_delivery_periods'],
                          report['days_with_price_conflicts']), (2, 48, 0))
        first, second = report['days']
        self.assertEqual([day['captures'] for day in report['days']], [2, 1])
        self.assertEqual([day['price_consensus'] for day in report['days']],
                         ['CONSISTENT', 'CONSISTENT'])
        self.assertEqual(first['conflicting_periods_utc'], [])
        self.assertEqual(first['first_observed_retrieved_at_utc'], '2026-11-19T10:17:54.057511+00:00')
        self.assertEqual(len({item['raw_sha256'] for item in first['evidence']}), 2)
        self.assertEqual(len({item['reference_sha256'] for item in first['evidence']}), 1)
        self.assertEqual(second['delivery_date'], '2026-11-21')
        completed = subprocess.run(self.command([*paths, paths[0]]), capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), report)
        self.assertEqual({path: path.read_bytes() for path in files}, before)

    def test_synthetic_conflict_identifies_exact_utc_period_without_choosing_or_rewriting_source(self):
        original = self.paths()[-1]
        original_files = {path: path.read_bytes() for path in original.parent.iterdir() if path.is_file()}
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'conflict'
            shutil.copytree(original.parent, target)
            manifest = target / 'manifest.json'
            data = json.loads(manifest.read_text())
            entry, = data['entries']
            raw_path = target / entry['raw_file']
            reference_path = target / entry['reference_file']
            raw = gzip.decompress(raw_path.read_bytes())
            reference = reference_path.read_bytes()
            self.assertEqual(raw.count(b'783,16'), 1)
            self.assertEqual(reference.count(b'783.16'), 1)
            raw = raw.replace(b'783,16', b'784,16', 1)
            reference = reference.replace(b'783.16', b'784.16', 1)
            raw_path.write_bytes(gzip.compress(raw, mtime=0))
            reference_path.write_bytes(reference)
            entry.update(raw_sha256=sha256(raw).hexdigest(),
                         reference_sha256=sha256(reference).hexdigest(), bytes=len(raw))
            manifest.write_text(json.dumps(data))
            before = {path: path.read_bytes() for path in target.iterdir()}
            report = audit_manifests([original, manifest])
            self.assertEqual(report, audit_manifests([manifest, original]))
            day, = report['days']
            self.assertEqual((report['delivery_days'], report['days_with_price_conflicts']), (1, 1))
            self.assertEqual((day['price_consensus'], day['captures'], day['periods']), ('CONFLICT', 2, 24))
            self.assertEqual(day['conflicting_periods_utc'], ['2026-11-20T23:00:00+00:00'])
            self.assertEqual(len(day['evidence']), 2)
            self.assertEqual({path: path.read_bytes() for path in target.iterdir()}, before)
        self.assertEqual({path: path.read_bytes() for path in original_files}, original_files)

    def test_bad_manifest_aborts_cli_without_partial_report(self):
        with self.assertRaisesRegex(ValueError, 'At least one'):
            audit_manifests([])
        with TemporaryDirectory() as temporary:
            bad = Path(temporary) / 'manifest.json'
            bad.write_text('{"entries": [], "errors": []}')
            first = self.paths()[0]
            for paths in ([first, bad], [bad, first]):
                completed = subprocess.run(self.command(paths), capture_output=True, text=True)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, '')
                self.assertIn('Incomplete capture manifest', completed.stderr)
