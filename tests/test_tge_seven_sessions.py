import csv
from datetime import date,timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import unittest
from tge_parser_safe import parse_complete_standard_day

FIX=Path(__file__).parent/'fixtures/tge_history_2026-10-27_2026-11-02'

class SevenHistoricalSessionsTests(unittest.TestCase):
    def manifest(self):return json.loads((FIX/'manifest.json').read_text())

    def test_seven_consecutive_delivery_dates_and_session_offset(self):
        m=self.manifest();self.assertEqual(m['errors'],[])
        expected=[date(2026,10,27)+timedelta(days=i) for i in range(7)]
        self.assertEqual([e['delivery_date'] for e in m['entries']],[d.isoformat() for d in expected])
        for e,d in zip(m['entries'],expected):
            self.assertEqual(e['session_date'],(d-timedelta(days=1)).isoformat())
            self.assertEqual(e['http_status'],200)

    def test_every_official_hour_matches_independent_lxml_reference(self):
        count=0
        for e in self.manifest()['entries']:
            with self.subTest(day=e['delivery_date']):
                raw=(FIX/e['raw_file']).read_bytes();ref=(FIX/e['reference_file']).read_bytes()
                self.assertEqual(sha256(raw).hexdigest(),e['raw_sha256'])
                self.assertEqual(sha256(ref).hexdigest(),e['reference_sha256'])
                expected={int(r['hour'][1:]):Decimal(r['price_pln_mwh']) for r in csv.DictReader(ref.decode().splitlines())}
                self.assertEqual(len(expected),24)
                actual=parse_complete_standard_day(raw.decode(),'{}'.format(e['delivery_date']))
                self.assertEqual({h:Decimal(str(v)) for h,v in actual.items()},expected)
                count+=len(actual)
        self.assertEqual(count,168)

    def test_wrong_delivery_date_is_rejected(self):
        e=self.manifest()['entries'][0];raw=(FIX/e['raw_file']).read_text()
        with self.assertRaises(ValueError):parse_complete_standard_day(raw,'2026-11-03')
