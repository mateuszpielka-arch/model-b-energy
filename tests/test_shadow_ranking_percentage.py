from copy import deepcopy
from decimal import Decimal, Overflow, getcontext, localcontext
import unittest

from shadow_scoring import _SHADOW_DECIMAL_CONTEXT
from shadow_walkforward import _rank


class RankingPercentageTests(unittest.TestCase):
    def test_percentage_intermediate_overflow_does_not_abort_finite_ranking(self):
        totals = dict(synthetic=dict(n=1000, nz=1000, ae=Decimal('3E999998'),
                                     err=Decimal('-3E999998'), denom=Decimal('6E999998'),
                                     ape=Decimal('3E999998')))
        before = deepcopy(totals)
        with localcontext(_SHADOW_DECIMAL_CONTEXT):
            maximum = getcontext().Emax
            row, = _rank(totals, 42)
            self.assertEqual(row['mae'], Decimal('3E999995'))
            self.assertEqual(row['bias'], Decimal('-3E999995'))
            self.assertEqual(row['wape_pct'], Decimal(50))
            self.assertEqual(row['mape_pct'], Decimal('3E999997'))
            self.assertEqual((row['days'], row['periods'], row['rank']), (42, 1000, 1))
            self.assertEqual(getcontext().Emax, maximum)
        self.assertEqual(totals, before)

    def test_percentage_still_rejects_truly_out_of_range_result(self):
        totals = dict(synthetic=dict(n=24, nz=24, ae=Decimal(0), err=Decimal(0),
                                     denom=Decimal(1), ape=Decimal('9E999999')))
        with localcontext(_SHADOW_DECIMAL_CONTEXT), self.assertRaises(Overflow):
            _rank(totals, 1)

    def test_zero_denominators_remain_explicitly_unscored(self):
        totals = dict(synthetic=dict(n=25, nz=0, ae=Decimal(0), err=Decimal(0),
                                     denom=Decimal(0), ape=Decimal(0)))
        with localcontext(_SHADOW_DECIMAL_CONTEXT):
            row, = _rank(totals, 1)
        self.assertIsNone(row['mape_pct'])
        self.assertIsNone(row['wape_pct'])
        self.assertEqual(row['zero_actual_periods'], 25)
