"""Preserve official TGE hourly labels. Does NOT assign DST periods to UTC."""
from datetime import date
from decimal import Decimal
import re
from shadow_scoring import expected_periods
from tge_parser_safe import _validated_rdn_rows

NUMBER=re.compile(r'[-−]?(?:\d{1,3}(?: \d{3})+|\d+)(?:,\d{1,4})?')


def parse_fix1_labels(html, day):
    """Complete ordered label/Decimal pairs for standard days and autumn 25h.

    Autumn order H01,H02,H02a,H03..H24 is grounded in captured TGE 25.10.2026.
    Spring labels and all DST UTC mappings remain blocked until independently verified.
    """
    d=date.fromisoformat(day); n=len(expected_periods(d))
    if n==23: raise ValueError('Spring TGE label convention not yet verified')
    expected=[f'H{h:02d}' for h in range(1,25)]
    if n==25: expected.insert(2,'H02a')
    result=[]; seen=set()
    for cells in _validated_rdn_rows(html):
        if not cells or not cells[0].startswith(day+'_H'): continue
        label=cells[0][len(day)+1:]
        if label not in expected: raise ValueError('Unsupported hourly label')
        if label in seen: raise ValueError('Duplicate hourly label')
        if len(cells)<3 or cells[1]!='60': raise ValueError('Missing hourly Fixing I cell/type')
        raw=cells[2].replace('\xa0',' ').replace('\u202f',' ').strip()
        if not NUMBER.fullmatch(raw): raise ValueError('Invalid Fixing I price')
        result.append((label,Decimal(raw.replace(' ','').replace('−','-').replace(',','.'))));seen.add(label)
    if [label for label,_ in result]!=expected:
        raise ValueError('Incomplete or reordered official hourly labels')
    return tuple(result)
