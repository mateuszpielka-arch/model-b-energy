import json, os, sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests
import xml.etree.ElementTree as ET

try:
    import psycopg
except ImportError:
    psycopg = None

PSE_BASE="https://api.raporty.pse.pl/api"
ENTSOE_BASE="https://web-api.tp.entsoe.eu/api"
DE_LU="10Y1001A1001A82H"
HEADERS={"User-Agent":"model-b-energy/0.3"}

def fetch_pse(endpoint, business_date):
    url=f"{PSE_BASE}/{endpoint}"; params={"$filter":f"business_date eq '{business_date}'"}
    rows=[]; pages=0
    while url:
        r=requests.get(url,params=params if pages==0 else None,headers={**HEADERS,"accept":"application/json"},timeout=30)
        print(json.dumps({"event":"http","source":"PSE","endpoint":endpoint,"status":r.status_code,"url":r.url}),flush=True)
        r.raise_for_status(); data=r.json(); rows.extend(data.get("value",[]))
        url=data.get("nextLink") or data.get("@odata.nextLink"); params=None; pages+=1
        if pages>100: raise RuntimeError("pagination guard")
    return rows

def entsoe_window(day):
    local=ZoneInfo("Europe/Warsaw")
    d=datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=local)
    e=d+timedelta(days=1)
    return d.astimezone(timezone.utc).strftime("%Y%m%d%H%M"), e.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

def parse_entsoe(xml_text):
    root=ET.fromstring(xml_text)
    def tag(x): return x.tag.split("}")[-1]
    series=[]
    for ts in [x for x in root.iter() if tag(x)=="TimeSeries"]:
        item={}
        for x in ts:
            t=tag(x)
            if t in ("mRID","currency_Unit.name","price_Measure_Unit.name","businessType","objectAggregation"):
                item[t]=x.text
        periods=[]
        for p in [x for x in ts.iter() if tag(x)=="Period"]:
            start=next((x.text for x in p.iter() if tag(x)=="start"),None)
            resolution=next((x.text for x in p.iter() if tag(x)=="resolution"),None)
            points=[]
            for pt in [x for x in p if tag(x)=="Point"]:
                vals={tag(z):z.text for z in pt}
                points.append(vals)
            periods.append({"start":start,"resolution":resolution,"points":points})
        item["periods"]=periods; series.append(item)
    return series

def fetch_entsoe(day, token):
    start,end=entsoe_window(day)
    queries={
      "de_lu_day_ahead_price":{"documentType":"A44","in_Domain":DE_LU,"out_Domain":DE_LU},
      "de_lu_load_forecast":{"documentType":"A65","processType":"A01","outBiddingZone_Domain":DE_LU},
    }
    out={}
    for name,q in queries.items():
        params={"securityToken":token,"periodStart":start,"periodEnd":end,**q}
        r=requests.get(ENTSOE_BASE,params=params,headers={**HEADERS,"accept":"application/xml"},timeout=45)
        safe_url=r.url.replace(token,"***") if token else r.url
        print(json.dumps({"event":"http","source":"ENTSOE","dataset":name,"status":r.status_code,"url":safe_url}),flush=True)
        if r.status_code!=200:
            print(json.dumps({"event":"entsoe_error","dataset":name,"body":r.text[:1000]}),flush=True)
            out[name]={"status":r.status_code,"error":r.text[:2000]}; continue
        parsed=parse_entsoe(r.text); out[name]={"status":200,"series":parsed}
        n=sum(len(p["points"]) for s in parsed for p in s["periods"])
        print(json.dumps({"event":"entsoe_summary","dataset":name,"series":len(parsed),"points":n}),flush=True)
        for s in parsed:
            for p in s["periods"]:
                for pt in p["points"]:
                    print(json.dumps({"event":"entsoe_point","dataset":name,"start":p["start"],"resolution":p["resolution"],**pt}),flush=True)
    return out

def archive_postgres(out):
    db_url=os.environ.get("DATABASE_URL")
    if not db_url:
        print(json.dumps({"event":"db_skip","reason":"DATABASE_URL not set"}),flush=True); return
    if psycopg is None:
        raise RuntimeError("DATABASE_URL is set but psycopg is not installed")
    retrieved=datetime.fromisoformat(out["retrieved_at_utc"])
    day=out["business_date"]
    payload=json.dumps(out,ensure_ascii=False)
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS raw")
            cur.execute("CREATE SCHEMA IF NOT EXISTS clean")
            cur.execute("CREATE SCHEMA IF NOT EXISTS model")
            cur.execute("""CREATE TABLE IF NOT EXISTS raw.snapshots (
                id BIGSERIAL PRIMARY KEY,
                business_date DATE NOT NULL,
                retrieved_at_utc TIMESTAMPTZ NOT NULL,
                source_version TEXT NOT NULL DEFAULT 'energy-collector-v3',
                payload JSONB NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (business_date, payload_hash)
            )""")
            import hashlib
            h=hashlib.sha256(payload.encode("utf-8")).hexdigest()
            cur.execute("""INSERT INTO raw.snapshots
                (business_date,retrieved_at_utc,payload,payload_hash)
                VALUES (%s,%s,%s::jsonb,%s)
                ON CONFLICT (business_date,payload_hash) DO NOTHING
                RETURNING id""",(day,retrieved,payload,h))
            row=cur.fetchone()
            print(json.dumps({"event":"db_archive","day":day,"inserted":bool(row),"id":row[0] if row else None}),flush=True)
        conn.commit()

def main():
    day=os.environ.get("BUSINESS_DATE") or (sys.argv[1] if len(sys.argv)>1 else None)
    if not day: raise SystemExit("Set BUSINESS_DATE=YYYY-MM-DD")
    out={"business_date":day,"retrieved_at_utc":datetime.now(timezone.utc).isoformat()}
    for ep in ("pk5l-wp","unav-pk5l"):
        rows=fetch_pse(ep,day); out[ep]=rows
        pubs=sorted({str(x.get("publication_ts")) for x in rows if x.get("publication_ts")})
        print(json.dumps({"event":"summary","endpoint":ep,"records":len(rows),"publication_ts":pubs}),flush=True)
    token=os.environ.get("ENTSOE_TOKEN")
    if token:
        out["entsoe"]=fetch_entsoe(day,token)
    else:
        print(json.dumps({"event":"entsoe_skip","reason":"ENTSOE_TOKEN not set"}),flush=True)
    os.makedirs("data",exist_ok=True)
    with open(f"data/snapshot_{day}.json","w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({"event":"saved","day":day}),flush=True)
    archive_postgres(out)

if __name__=="__main__": main()
