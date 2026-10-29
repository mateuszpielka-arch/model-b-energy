"""Strict hourly Fixing I parser. No database or network side effects."""
import re
from html.parser import HTMLParser

class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_fix1(html: str, day: str) -> dict[int, float]:
    """Read only hourly rows YYYY-MM-DD_H01..H24; 60 is instrument type.

    Returns a partial map if the page is incomplete. Duplicate hour rows fail
    closed; 25-hour DST days require separate period identifiers and schema.
    """
    parser = _Rows()
    parser.feed(html)
    prices = {}
    key = re.compile(rf"{re.escape(day)}_H(\d{{2}})")
    number = re.compile(r"[-−]?(?:\d{1,3}(?: \d{3})+|\d+)(?:,\d{1,4})?")
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        match = key.fullmatch(cells[0])
        if not match or cells[1] != "60":
            continue
        hour = int(match.group(1))
        if not 1 <= hour <= 24:
            raise ValueError(f"Unsupported hour {hour}; DST requires separate handling")
        raw = cells[2].replace("\xa0", " ").replace("\u202f", " ").strip()
        if not number.fullmatch(raw):
            continue
        if hour in prices:
            raise ValueError(f"Duplicate hourly key H{hour:02d}; fail closed")
        prices[hour] = float(raw.replace(" ", "").replace("−", "-").replace(",", "."))
    return prices
