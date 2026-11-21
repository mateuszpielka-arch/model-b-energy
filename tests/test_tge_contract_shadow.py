"""Offline fail-closed regression tests for the RDN Fixing I parser.

No network, database, or production side effects.
Run: python -m unittest discover -s tests -p 'test_tge_contract_shadow.py'
"""
import unittest
from decimal import Decimal
from tge_parser_safe import parse_complete_standard_day

DAY = "2026-11-21"

def html(rows, *, header="Kurs [PLN/MWh]"):
    head = ('<thead><tr><th></th><th></th><th colspan="2">Fixing I</th></tr>'
            '<tr><th>Data dostawy</th><th>Typ instrumentu</th>'
            f'<th>{header}</th><th>Wolumen [MW]</th></tr></thead>')
    return '<table id="rdn">' + head + '<tbody>' + ''.join(
        f'<tr><td>{key}</td><td>{typ}</td><td>{price}</td><td>1</td></tr>'
        for key, typ, price in rows
    ) + '</tbody></table>'

def full():
    return [(f"{DAY}_H{h:02d}", "60", "123,45") for h in range(1,25)]

class ContractTests(unittest.TestCase):
    def test_complete_exact(self):
        prices = parse_complete_standard_day(html(full()), DAY, exact=True)
        self.assertEqual(len(prices),24)
        self.assertEqual(prices[1],Decimal("123.45"))
    def test_missing_hour_rejected(self):
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            parse_complete_standard_day(html(full()[:-1]),DAY)
    def test_duplicate_hour_rejected(self):
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            parse_complete_standard_day(html(full()+[full()[0]]),DAY)
    def test_wrong_instrument_rejected(self):
        rows=full(); rows[0]=(rows[0][0],"61",rows[0][2])
        with self.assertRaisesRegex(ValueError,"wrong instrument"):
            parse_complete_standard_day(html(rows),DAY)
    def test_changed_header_rejected(self):
        with self.assertRaisesRegex(ValueError,"columns changed"):
            parse_complete_standard_day(html(full(),header="Volume"),DAY)
    def test_unpublished_dash_rejected(self):
        rows=full(); rows[0]=(rows[0][0],"60","-")
        with self.assertRaisesRegex(ValueError,"Invalid"):
            parse_complete_standard_day(html(rows),DAY)
    def test_negative_price_allowed(self):
        rows=full(); rows[0]=(rows[0][0],"60","-12,50")
        prices=parse_complete_standard_day(html(rows),DAY,exact=True)
        self.assertEqual(prices[1],Decimal("-12.50"))
    def test_dst_day_blocked(self):
        with self.assertRaisesRegex(ValueError,"DST"):
            parse_complete_standard_day(html(full()),"2026-10-25")

if __name__=="__main__":
    unittest.main()
