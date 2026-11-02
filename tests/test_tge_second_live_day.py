import csv
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest
from shadow_archive_batch import prepare_batch, SOURCE_URL
from tge_parser_safe import parse_complete_standard_day

FIX = Path(__file__).parent/'fixtures'

class SecondRealDayTests(unittest.TestCase):
    def args(self):
        raw=(FIX/'tge_rdn_captured_2026-11-02.html').read_bytes()
        ref=(FIX/'tge_fix1_2026-11-03_reference.csv').read_bytes()
        manifest=json.loads((FIX/'tge_rdn_captured_2026-11-02.json').read_text())
        rows=csv.DictReader(x for x in ref.decode().splitlines() if not x.startswith('#'))
        return raw,ref,manifest,list(rows)

    def test_all_24_prices_against_independent_reference(self):
        raw,_,_,rows=self.args()
        parsed=parse_complete_standard_day(raw.decode(),'2026-11-03')
        expected={int(r['hour'][1:]):Decimal(r['price_pln_mwh']) for r in rows}
        self.assertEqual({h:Decimal(str(v)) for h,v in parsed.items()},expected)

    def test_evidence_hashes_and_batch_admission(self):
        raw,ref,m,rows=self.args()
        self.assertEqual(sha256(raw).hexdigest(),m['sha256'])
        self.assertEqual(sha256(ref).hexdigest(),m['reference_sha256'])
        batch=prepare_batch(raw=raw,day=m['delivery_date'],source_url=SOURCE_URL,
                            retrieved_at=datetime.fromisoformat(m['retrieved_at_utc']),
                            reference_rows=[(r['hour'],r['price_pln_mwh']) for r in rows],
                            reference_sha256=m['reference_sha256'])
        self.assertEqual(len(batch.rows),24)
        self.assertEqual(batch.rows[0][1],Decimal('1021.75'))

    def test_continuous_prices_are_not_used(self):
        raw,_,_,_=self.args(); parsed=parse_complete_standard_day(raw.decode(),'2026-11-03')
        # Official row has Fixing I=1021.75 and continuous=1050.00 for H01.
        self.assertEqual(parsed[1],1021.75)
        self.assertNotEqual(parsed[1],1050.00)
        self.assertEqual(parsed[7],1291.41)
        self.assertNotEqual(parsed[7],1266.41)

    def test_missing_date_does_not_reuse_present_day(self):
        raw,_,_,_=self.args()
        with self.assertRaises(ValueError):
            parse_complete_standard_day(raw.decode(),'2026-11-02')
