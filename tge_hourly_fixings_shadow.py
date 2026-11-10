"""Read-only SHADOW extraction of hourly TGE RDN Fixing I and II.

No network or database writes. Reject ambiguous headers rather than guessing.
"""
import re
from html.parser import HTMLParser

class _Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows=[]; self.row=None; self.cell=None
    def handle_starttag(self, tag, attrs):
        if tag=="tr": self.row=[]
        elif tag in ("th","td") and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ("th","td") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split())); self.cell=None
        elif tag=="tr" and self.row is not None:
            self.rows.append(self.row); self.row=None

def parse_hourly_fixings(html, delivery_date, fixing1_column=2, fixing2_column=None):
    """Extract hourly prices; column indexes must be verified against real HTML.

    fixing2_column=None means Fixing II is NOT guessed from table position.
    Return {'fixing_i': {hour: price}, 'fixing_ii': {hour: price}}.
    Incomplete snapshots remain incomplete; callers must check 24/24.
    """
    table=_Table(); table.feed(html)
    result={"fixing_i":{}, "fixing_ii":{}}
    seen=set()
    key=re.compile(re.escape(delivery_date)+r"_H(\d{2})")
    price_re=re.compile(r"[-−]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:,\d{1,4})?")
    for cells in table.rows:
        if len(cells)<2: continue
        m=key.fullmatch(cells[0])
        if not m or cells[1]!="60": continue
        hour=int(m.group(1))
        if hour<1 or hour>24: raise ValueError("Unsupported hour/DST")
        if hour in seen: raise ValueError("Duplicate hourly instrument")
        seen.add(hour)
        for name,index in (("fixing_i",fixing1_column),("fixing_ii",fixing2_column)):
            if index is None: continue
            if index<2 or index>=len(cells): raise ValueError("Unverified/missing price column")
            raw=cells[index].replace("\u00a0"," ").replace("\u202f"," ").strip()
            if raw in ("","-"): continue
            if not price_re.fullmatch(raw): raise ValueError("Invalid price cell")
            result[name][hour]=float(raw.replace(" ","").replace("−","-").replace(",","."))
    return result
