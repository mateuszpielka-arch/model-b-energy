from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest
from tge_parser_safe import parse_fix1,parse_complete_standard_day
from tge_period_labels import parse_fix1_labels
from tools.capture_tge_validation import extract_reference

class AutumnLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Path(__file__).parent/'fixtures/tge_rdn_session_2026-10-24_captured_2026-11-02.html'
        cls.raw = cls.fixture.read_bytes()
        cls.html = cls.raw.decode('utf-8')

    def test_actual_25_labels_include_H02a_and_negative_prices(self):
        rows=parse_fix1_labels(self.html,'2026-10-25')
        self.assertEqual(len(rows),25)
        self.assertEqual(rows[:4],(('H01',Decimal('614.00')),('H02',Decimal('478.69')),
                                  ('H02a',Decimal('380.01')),('H03',Decimal('380.00'))))
        self.assertEqual(dict(rows)['H10'],Decimal('-13.46'))
        self.assertEqual(rows[-1],('H24',Decimal('370.01')))

    def test_legacy_integer_parser_cannot_silently_drop_H02a(self):
        with self.assertRaisesRegex(ValueError,'Unsupported hourly label'):parse_fix1(self.html,'2026-10-25')

    def test_all_25_fixing_i_prices_match_independent_label_extraction(self):
        metadata = json.loads(self.fixture.with_suffix('.json').read_text())
        day = metadata['delivery_date']
        self.assertEqual(sha256(self.raw).hexdigest(), metadata['raw_sha256'])
        reference = extract_reference(self.raw, day, autumn_25h=True)
        self.assertEqual(len(reference), 25)
        self.assertEqual(parse_fix1_labels(self.html, day), tuple(reference))
        with self.assertRaisesRegex(ValueError, 'Unexpected hourly label'):
            extract_reference(self.raw, day)
        missing = self.raw.replace(b'2026-10-25_H02a', b'2026-10-25_Q02a:00')
        with self.assertRaisesRegex(ValueError, 'Incomplete or reordered'):
            extract_reference(missing, day, autumn_25h=True)

    def test_standard_admission_still_blocks_dst(self):
        with self.assertRaisesRegex(ValueError,'DST'):parse_complete_standard_day(self.html,'2026-10-25')

    def test_missing_extra_period_rejected(self):
        html=self.html.replace('2026-10-25_H02a','2026-10-25_Q01a:00')
        with self.assertRaises(ValueError):parse_fix1_labels(html,'2026-10-25')

    def test_duplicate_extra_period_rejected(self):
        html=self.html.replace('2026-10-25_H03','2026-10-25_H02a')
        with self.assertRaisesRegex(ValueError,'Duplicate'):parse_fix1_labels(html,'2026-10-25')

    def test_spring_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError,'Spring'):parse_fix1_labels('', '2026-03-29')

    def test_standard_real_day_remains_supported(self):
        html=(Path(__file__).parent/'fixtures/tge_rdn_captured_2026-11-02.html').read_text()
        self.assertEqual(len(parse_fix1_labels(html,'2026-11-03')),24)
