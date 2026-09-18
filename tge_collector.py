import json, os, re, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import psycopg

TGE_URL = "https://tge.pl/energia-elektryczna-rdn"
HEADERS = {"User-Agent": "Mozilla/5.0 model-b-energy-tge/1.0", "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8"}
WARSAW = ZoneInfo("Europe/Warsaw")

def _num(s):
    return float(s.replace("\xa0","").replace(" ","").replace(",", "."))

def parse_fix1(html, delivery_date):
    # TGE page exposes hourly rows as YYYY-MM-DD_H01 ... H24 and Fixing I values in the rendered HTML.
    # Keep parsing deliberately strict: only accept exactly 24 distinct hours for requested delivery date.
    rows = {}
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    for h in range(1,25):
        key = f"{delivery_date}_H{h:02d}"
        p = text.find(key)
        if p < 0:
            continue
        chunk = text[p:p+1800]
        nums = re.findall(r"[-−]?\d{1,3}(?:[ .]\d{3})*(?:,\d+)?|[-−]?\d+(?:,\d+)?", chunk)
        # First numeric token(s) include date/hour. Find plausible PLN/MWh value after the key.
        after = chunk[len(key):]
        vals = re.findall(r"[-−]?\d+(?:[ .]\d{3})*(?:,\d+)?", after)
        for raw in vals:
            raw = raw.replace("−","-")
            try:
                v = _num(raw)
            except ValueError:
                continue
            if -5000 <= v <= 20000:
                rows[h] = v
                break
    return rows

def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS actual")
        cur.execute("""CREATE TABLE IF NOT EXISTS actual.tge_fix1 (
          delivery_date DATE NOT NULL,
          hour SMALLINT NOT NULL CHECK (hour BETWEEN 1 AND 24),
          price_pln_mwh NUMERIC(12,4) NOT NULL,
          source_url TEXT NOT NULL,
          first_seen_at_utc TIMESTAMPTZ NOT NULL,
          retrieved_at_utc TIMESTAMPTZ NOT NULL,
          raw_hash TEXT,
          PRIMARY KEY (delivery_date, hour)
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS actual.tge_poll_log (
          id BIGSERIAL PRIMARY KEY,
          delivery_date DATE NOT NULL,
          retrieved_at_utc TIMESTAMPTZ NOT NULL,
          row_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          detail TEXT
        )""")
    conn.commit()

def run_once(delivery_date):
    now = datetime.now(timezone.utc)
    r = requests.get(TGE_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = parse_fix1(r.text, delivery_date)
    import hashlib
    raw_hash = hashlib.sha256(r.content).hexdigest()
    db = os.environ["DATABASE_URL"]
    with psycopg.connect(db) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            status = "COMPLETE" if len(rows)==24 else "WAITING"
            cur.execute("INSERT INTO actual.tge_poll_log(delivery_date,retrieved_at_utc,row_count,status,detail) VALUES(%s,%s,%s,%s,%s)",
                        (delivery_date, now, len(rows), status, f"HTTP {r.status_code}"))
            if len(rows)==24:
                for h,v in sorted(rows.items()):
                    cur.execute("""INSERT INTO actual.tge_fix1(delivery_date,hour,price_pln_mwh,source_url,first_seen_at_utc,retrieved_at_utc,raw_hash)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT(delivery_date,hour) DO NOTHING""",
                      (delivery_date,h,v,TGE_URL,now,now,raw_hash))
        conn.commit()
    print(json.dumps({"event":"tge_fix1","delivery_date":delivery_date,"rows":len(rows),"status":"COMPLETE" if len(rows)==24 else "WAITING","retrieved_at_utc":now.isoformat()}), flush=True)
    return len(rows)==24

def main():
    delivery_date = os.environ.get("DELIVERY_DATE")
    if not delivery_date:
        # RDN result published today concerns next delivery day.
        delivery_date = datetime.now(WARSAW).date().isoformat()
        from datetime import timedelta
        delivery_date = (datetime.now(WARSAW).date()+timedelta(days=1)).isoformat()
    # Railway cron invokes every 15 min. Outside the polling window exit cheaply.
    local = datetime.now(WARSAW)
    if local.hour < 10 or (local.hour == 10 and local.minute < 35):
        print(json.dumps({"event":"skip","reason":"before 10:35 Europe/Warsaw"})); return
    run_once(delivery_date)

if __name__ == "__main__":
    main()
