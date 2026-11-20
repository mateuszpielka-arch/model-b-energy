from hashlib import sha256
import json
from pathlib import Path
import unittest

from shadow_manifest_audit import audit_manifests


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / 'docs/validation/fix1_capture_consensus_2026-11-21.json'


class RetainedFix1CorpusTests(unittest.TestCase):
    def test_retained_28_captures_reproduce_full_consensus_without_source_writes(self):
        checkpoint = json.loads(CHECKPOINT.read_text())
        paths = [ROOT / label for label in checkpoint['manifests']]
        self.assertEqual(len(paths), 20)
        files = {path for manifest in paths for path in manifest.parent.iterdir() if path.is_file()}
        before = {path: sha256(path.read_bytes()).hexdigest() for path in files}
        actual = audit_manifests(paths)
        self.assertEqual(actual, checkpoint['audit'])
        self.assertEqual(actual, audit_manifests(reversed(paths)))
        self.assertEqual((actual['validated_capture_entries'], actual['distinct_captures'],
                          actual['delivery_days'], actual['unique_delivery_periods'],
                          actual['days_with_price_conflicts']), (28, 28, 26, 624, 0))
        self.assertEqual([(day['delivery_date'], day['captures']) for day in actual['days']
                          if day['captures'] > 1], [('2026-11-19', 2), ('2026-11-20', 2)])
        self.assertEqual({path: sha256(path.read_bytes()).hexdigest() for path in files}, before)

    def test_repeated_corpus_paths_only_increase_duplicate_entry_count(self):
        checkpoint = json.loads(CHECKPOINT.read_text())
        paths = [ROOT / label for label in checkpoint['manifests']]
        actual = audit_manifests([*paths, *reversed(paths)])
        self.assertEqual((actual['validated_capture_entries'], actual['duplicate_capture_entries']),
                         (56, 28))
        expected = {**checkpoint['audit'], 'validated_capture_entries': 56,
                    'duplicate_capture_entries': 28}
        self.assertEqual(actual, expected)
