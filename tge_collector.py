import json, os, re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests, psycopg

TGE_URL="https://tge.pl/energia-elektryczna-rdn"
HEADERS={"User-Agent":"Mozilla/5.0 model-b-energy-tge/1.1","Accept-Language":"pl-PL,pl;q=0.9,en;q=0.8"}
WARSAW=ZoneInfo("Europe/Warsaw")

def _num(s): return float(s.replace("\xa0","").replace(" ","").replace("−","-").replace(",","."))

def parse_fix1(html,day):
    text=re.sub(r"<[^>]+>"," | ",html); text=re.sub(r"\s+"," ",text)
    rows={}
    for h in range(1,25):
        key=f"{day}_H{h:02d}"
        p=text.find(key)
        if p<0: continue
        # Hourly row is: delivery key | instrument type 60 | Fixing I price.
        m=re.search(re.escape(key)+r"\s*\|?\s*60\s*\|?\s*([-−]?\d+(?:[ .]\d{3})*(?:,\d+)?)",text[p:p+500])
        if m: rows[h]=_num(m.group(1))
    return rows

def ensure_schema(conn):
    with conn.cursor() as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS actual")
        c.execute("""CREATE TABLE IF NOT EXISTS actual.tge_fix1(delivery_date DATE NOT NULL,hour SMALLINT NOT NULL CHECK(hour BETWEEN 1 AND 24),price_pln_mwh NUMERIC(12,4) NOT NULL,source_url TEXT NOT NULL,first_seen_at_utc TIMESTAMPTZ NOT NULL,retrieved_at_utc TIMESTAMPTZ NOT NULL,raw_hash TEXT,PRIMARY KEY(delivery_date,hour))""")
        c.execute("""CREATE TABLE IF NOT EXISTS actual.tge_poll_log(id BIGSERIAL PRIMARY KEY,delivery_date DATE NOT NULL,retrieved_at_utc TIMESTAMPTZ NOT NULL,row_count INTEGER NOT NULL,status TEXT NOT NULL,detail TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS model.forecast_scores(forecast_id BIGINT NOT NULL REFERENCES model.forecasts(id),hour SMALLINT NOT NULL,forecast_price NUMERIC(12,4) NOT NULL,actual_price NUMERIC(12,4) NOT NULL,error NUMERIC(12,4) NOT NULL,abs_error NUMERIC(12,4) NOT NULL,sq_error NUMERIC(18,4) NOT NULL,scored_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(forecast_id,hour))""")
    conn.commit()

def score(conn,day):
    with conn.cursor() as c:
        c.execute("SELECT id,prices FROM model.forecasts WHERE business_date=%s AND status='FROZEN' ORDER BY id LIMIT 1",(day,)); f=c.fetchone()
        if not f: return None
        fid,prices=f; fp={int(x['hour']):float(x['price']) for x in prices}
        c.execute("SELECT hour,price_pln_mwh FROM actual.tge_fix1 WHERE delivery_date=%s ORDER BY hour",(day,)); actual={int(h):float(v) for h,v in c.fetchall()}
        if len(actual)!=24: return None
        errs=[]
        for h in range(1,25):
            e=fp[h]-actual[h]; errs.append(e)
            c.execute("""INSERT INTO model.forecast_scores(forecast_id,hour,forecast_price,actual_price,error,abs_error,sq_error) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(forecast_id,hour) DO NOTHING""",(fid,h,fp[h],actual[h],e,abs(e),e*e))
        import math
        out={'forecast_id':fid,'mae':sum(abs(x) for x in errs)/24,'rmse':math.sqrt(sum(x*x for x in errs)/24),'bias':sum(errs)/24}
    conn.commit(); return out

def run_once(day):
    now=datetime.now(timezone.utc); r=requests.get(TGE_URL,headers=HEADERS,timeout=30); r.raise_for_status(); rows=parse_fix1(r.text,day)
    import hashlib; raw_hash=hashlib.sha256(r.content).hexdigest(); db=os.environ['DATABASE_URL']
    with psycopg.connect(db) as conn:
        ensure_schema(conn)
        with conn.cursor() as c:
            status='COMPLETE' if len(rows)==24 else 'WAITING'; c.execute("INSERT INTO actual.tge_poll_log(delivery_date,retrieved_at_utc,row_count,status,detail) VALUES(%s,%s,%s,%s,%s)",(day,now,len(rows),status,f'HTTP {r.status_code}'))
            if len(rows)==24:
                # Correct any prior collector value only when current page is a complete official 24/24 snapshot.
                for h,v in sorted(rows.items()):
                    c.execute("""INSERT INTO actual.tge_fix1(delivery_date,hour,price_pln_mwh,source_url,first_seen_at_utc,retrieved_at_utc,raw_hash) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(delivery_date,hour) DO UPDATE SET price_pln_mwh=EXCLUDED.price_pln_mwh,retrieved_at_utc=EXCLUDED.retrieved_at_utc,raw_hash=EXCLUDED.raw_hash""",(day,h,v,TGE_URL,now,now,raw_hash))
        conn.commit(); metrics=score(conn,day) if len(rows)==24 else None
    print(json.dumps({'event':'tge_fix1','delivery_date':day,'rows':len(rows),'status':'COMPLETE' if len(rows)==24 else 'WAITING','sample':rows,'score':metrics,'retrieved_at_utc':now.isoformat()}),flush=True)

def main():
    day=os.environ.get('DELIVERY_DATE') or (datetime.now(WARSAW).date()+timedelta(days=1)).isoformat(); local=datetime.now(WARSAW)
    if local.hour<10 or (local.hour==10 and local.minute<35): print(json.dumps({'event':'skip','reason':'before 10:35 Europe/Warsaw'})); return
    run_once(day)
if __name__=='__main__': main()
