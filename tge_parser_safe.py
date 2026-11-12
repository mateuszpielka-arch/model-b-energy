"""Strict hourly Fixing I parser. No database or network side effects."""
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
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
    date.fromisoformat(day)
    parser = _Rows()
    parser.feed(html)
    prices = {}
    key = re.compile(rf"{re.escape(day)}_H(\d{{2}})")
    number = re.compile(r"[-−]?(?:\d{1,3}(?: \d{3})+|\d+)(?:,\d{1,4})?")
    for cells in parser.rows:
        if len(cells) < 3:
            continue
        match = key.fullmatch(cells[0])
        if not match and cells[0].startswith(day + "_H"):
            raise ValueError(f"Unsupported hourly label {cells[0]}; preserve labels before DST mapping")
        if not match or cells[1] != "60":
            continue
        hour = int(match.group(1))
        if not 1 <= hour <= 24:
            raise ValueError(f"Unsupported hour {hour}; DST requires separate handling")
        raw = cells[2].replace("\xa0", " ").replace("\u202f", " ").strip()
        if not number.fullmatch(raw):
            raise ValueError(f"Invalid Fixing I price for H{hour:02d}: {raw!r}")
        if hour in prices:
            raise ValueError(f"Duplicate hourly key H{hour:02d}; fail closed")
        prices[hour] = float(raw.replace(" ", "").replace("−", "-").replace(",", "."))
    return prices


class _FixingTable(_Rows):
    """Read the RDN header and body from their respective table sections."""

    SECTIONS = ("thead", "tbody", "tfoot")
    def __init__(self):
        super().__init__()
        self.active = False
        self.count = 0
        self.closed = False
        self.header_rows = []
        self.header_spans = []
        self.span = 1
        self.section = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            if self.active:
                raise ValueError("Nested table in RDN; unsupported layout")
            if attrs.get("id") == "rdn":
                self.count += 1
                self.active = True
            return
        if not self.active:
            return
        if tag in self.SECTIONS:
            if self.section is not None or self.row is not None:
                raise ValueError("Unclosed RDN table section")
            self.section = tag
        if tag == "tr":
            if self.row is not None:
                raise ValueError("Unclosed RDN row")
            self.header_spans = []
        if tag in ("td", "th"):
            if self.cell is not None:
                raise ValueError("Unclosed RDN cell")
            self.span = int(attrs.get("colspan", "1"))
            if not 1 <= self.span <= 30 or attrs.get("rowspan", "1") != "1":
                raise ValueError("Unsupported RDN header span")
            if tag == "td" and self.span != 1:
                raise ValueError("Unsupported RDN data cell colspan")
        super().handle_starttag(tag, list(attrs.items()))

    def handle_data(self, data):
        if self.active:
            super().handle_data(data)

    def handle_endtag(self, tag):
        if not self.active:
            return
        if tag == "table":
            if self.section is not None or self.row is not None or self.cell is not None:
                raise ValueError("Incomplete RDN table")
            self.active = False
            self.closed = True
            return
        if tag in self.SECTIONS:
            if self.section != tag or self.row is not None or self.cell is not None:
                raise ValueError("Unclosed RDN table section")
            self.section = None
            return
        if tag == "tr" and self.row is not None:
            if self.cell is not None:
                raise ValueError("Unclosed RDN cell")
            if self.section == "thead":
                self.header_rows.append((self.row, tuple(self.header_spans)))
            elif self.section == "tbody":
                self.rows.append(self.row)
            self.row = None
            return
        super().handle_endtag(tag)
        if tag == "th" and self.row is not None:
            self.header_spans.append(self.span)
            self.row.extend([self.row[-1]] * (self.span - 1))


def _validated_rdn_rows(html: str):
    """Fail closed unless the captured RDN Fixing I column contract matches.

    This validates layout, not authenticity of the HTTP source or archive approval.
    """
    table = _FixingTable()
    table.feed(html)
    table.close()
    if table.count != 1 or not table.closed or table.active:
        raise ValueError("Expected one complete table id=rdn")
    if len(table.header_rows) != 2:
        raise ValueError("Expected two RDN header rows")
    (groups, group_spans), (columns, column_spans) = table.header_rows
    if (group_spans[:3] != (1, 1, 2) or sum(group_spans) != len(groups) or
        column_spans != (1,) * len(columns) or
        groups[:4] != ["", "", "Fixing I", "Fixing I"] or
        groups.count("Fixing I") != 2 or
        columns[:4] != ["Data dostawy", "Typ instrumentu", "Kurs [PLN/MWh]", "Wolumen [MW]"] or
        len(groups) != len(columns)):
        raise ValueError("RDN Fixing I columns changed; admission blocked")
    for row in table.rows:
        if row and re.match(r"^\d{4}-\d{2}-\d{2}_H", row[0]) and len(row) != len(columns):
            raise ValueError("Hourly RDN row width does not match header")
    return table.rows


def parse_verified_layout(html: str, day: str) -> dict[int, float]:
    """Parse integer-hour map only from the verified RDN Fixing I layout."""
    rows = _validated_rdn_rows(html)
    for row in rows:
        if row and row[0].startswith(day + "_H") and (len(row) < 2 or row[1] != "60"):
            raise ValueError(f"Hourly RDN row has wrong instrument type: {row[0]}")
    # Reuse the strict numeric/duplicate validator only for this selected table.
    from html import escape
    selected = "".join("<tr>" + "".join("<td>" + escape(c) + "</td>" for c in row) + "</tr>"
                       for row in rows)
    return parse_fix1(selected, day)


def parse_complete_standard_day(html: str, day: str) -> dict[int, float]:
    """SHADOW admission guard. DST blocked until official period mapping is tested.

    Requires the official RDN table/header layout; HTTP provenance remains separate.
    """
    d = date.fromisoformat(day)
    tz = ZoneInfo("Europe/Warsaw")
    start = datetime.combine(d, time(), tz).astimezone(timezone.utc)
    end = datetime.combine(d + timedelta(days=1), time(), tz).astimezone(timezone.utc)
    if (end - start).total_seconds() != 86400:
        raise ValueError("DST day requires verified period mapping; admission blocked")
    rows = parse_verified_layout(html, day)
    if set(rows) != set(range(1, 25)):
        raise ValueError("Incomplete hourly Fixing I snapshot; admission blocked")
    return rows
