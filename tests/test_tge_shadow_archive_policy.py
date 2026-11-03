"""Offline regression: verified promotion never overwrites existing prices."""
import unittest
from tge_shadow_archive_policy import plan_verified_promotion
from test_tge_fix1_20261104 import load_reference

class ArchivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.reference = load_reference()

    def test_new_verified_day_is_24_append_only_inserts(self):
        p = plan_verified_promotion(self.reference, self.reference, {})
        self.assertEqual(p['status'], 'APPEND_ONLY')
        self.assertEqual(len(p['insert_rows']), 24)

    def test_idempotent_same_values_produce_no_inserts(self):
        p = plan_verified_promotion(self.reference, self.reference, self.reference)
        self.assertEqual(p['status'], 'ALREADY_VERIFIED')
        self.assertEqual(p['insert_rows'], [])

    def test_conflicting_verified_hour_quarantines_entire_day(self):
        old = {14: 60.0}
        p = plan_verified_promotion(self.reference, self.reference, old)
        self.assertEqual(p['status'], 'QUARANTINE_IMMUTABLE_CONFLICT')
        self.assertEqual(p['conflict_hours'], [14])
        self.assertEqual(p['insert_rows'], [])

    def test_wrong_reference_quarantines_without_inserts(self):
        bad = dict(self.reference); bad[20] = 2026.0
        p = plan_verified_promotion(bad, self.reference, {})
        self.assertEqual(p['status'], 'QUARANTINE_REFERENCE_MISMATCH')
        self.assertEqual(p['conflict_hours'], [20])
        self.assertEqual(p['insert_rows'], [])

    def test_partial_verified_day_only_adds_missing(self):
        old = {h: self.reference[h] for h in range(1, 13)}
        p = plan_verified_promotion(self.reference, self.reference, old)
        self.assertEqual(p['status'], 'APPEND_ONLY')
        self.assertEqual([h for h, _ in p['insert_rows']], list(range(13, 25)))

    def test_missing_candidate_hour_fails_closed(self):
        bad = dict(self.reference); bad.pop(16)
        with self.assertRaisesRegex(ValueError, '24 unique hours'):
            plan_verified_promotion(bad, self.reference, {})

if __name__ == '__main__':
    unittest.main()
