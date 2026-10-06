import json, os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import psycopg

MODEL_VERSION = "jwcd-linear-hourly-90d-v1-20261006"
JWCD_FIELD = "fcst_gen_unit_stor_prov"

# Hour-specific OLS: Fix1 = intercept + slope * JWCD.
# Trained on the latest 90 complete days available in history_jwcd_fix1.csv
# (2026-07-05..2026-10-02). No Ridge, PV, wind or ENTSO-E variables.
COEF = {
1:(341.5658183251,0.0317589509347),2:(315.2207647625,0.0331139375655),
3:(287.1496103460,0.0349337083301),4:(292.4496552495,0.0335416108527),
5:(305.6122843654,0.0323878621583),6:(314.2109184796,0.0330783360641),
7:(233.4256511758,0.0456696888781),8:(132.8857912120,0.0555670573121),
9:(53.6453966650,0.0580140104315),10:(-73.9153317402,0.0683098846238),
11:(-146.7583042306,0.0744552185170),12:(-161.1403013605,0.0704332021771),
13:(-189.9402136074,0.0681064449326),14:(-199.6931993094,0.0681695292392),
15:(-183.4246903294,0.0701782276086),16:(-179.3141010081,0.0736590007665),
17:(-69.3804040975,0.0648226414021),18:(60.1484226489,0.0593897115377),
19:(-110.6115715896,0.0783034246327),20:(-743.0722879853,0.1313738036829),
21:(-581.2135025357,0.1136955072727),22:(-3.2426504851,0.0637927758540),
23:(317.7042561980,0.0357637347203),24:(378.5444217119,0.0273939479754)
}

def latest_snapshot(conn, day):
    with conn.cursor() as cur:
        cur.execute("SELECT id,payload,retrieved_at_utc FROM raw.snapshots WHERE business_date=%s ORDER BY retrieved_at_utc DESC LIMIT 1",(day,))
        row=cur.fetchone()
    if not row: raise RuntimeError(f"No snapshot for {day}")
    return row

def hour_no(row):
    p=str(row.get("period",""))
    try: return int(p.split("-")[0].strip())+1
    except: return int(str(row.get("plan_dtime"))[11:13])+1

def validate(payload):
    rows=payload.get("pk5l-wp",[])
    if len(rows)!=24: raise RuntimeError(f"PK5L expected 24 rows, got {len(rows)}")
    rows=sorted(rows,key=hour_no)
    hours=[hour_no(r) for r in rows]
    if hours!=list(range(1,25)): raise RuntimeError(f"Invalid hourly sequence: {hours}")
    missing=[hour_no(r) for r in rows if r.get(JWCD_FIELD) is None]
    if missing: raise RuntimeError(f"Missing {JWCD_FIELD} for hours {missing}")
    return rows

def predict_24(payload):
    out=[]
    for r in validate(payload):
        h=hour_no(r); jwcd=float(r[JWCD_FIELD]); intercept,slope=COEF[h]
        price=intercept+slope*jwcd
        out.append({"hour":h,"jwcd_mw":round(jwcd,2),"price":round(price,2)})
    return out

def publication_ts(payload):
    vals=[r.get("publication_ts") for r in payload.get("pk5l-wp",[]) if r.get("publication_ts")]
    return max(vals) if vals else None

def persist(conn,day,snapshot_id,prices,status,pub_ts):
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS model")
        cur.execute("""CREATE TABLE IF NOT EXISTS model.forecasts (
          id BIGSERIAL PRIMARY KEY,business_date DATE NOT NULL,snapshot_id BIGINT NOT NULL REFERENCES raw.snapshots(id),
          model_version TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('DRAFT','FROZEN')),
          prices JSONB NOT NULL,publication_ts TEXT,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(business_date,snapshot_id,model_version,status))""")
        if status=="FROZEN":
            cur.execute("SELECT id FROM model.forecasts WHERE business_date=%s AND status='FROZEN' AND model_version=%s LIMIT 1",(day,MODEL_VERSION))
            old=cur.fetchone()
            if old: return old[0]
        cur.execute("""INSERT INTO model.forecasts(business_date,snapshot_id,model_version,status,prices,publication_ts)
          VALUES(%s,%s,%s,%s,%s::jsonb,%s)
          ON CONFLICT(business_date,snapshot_id,model_version,status)
          DO UPDATE SET prices=EXCLUDED.prices,publication_ts=EXCLUDED.publication_ts,created_at=now() RETURNING id""",
          (day,snapshot_id,MODEL_VERSION,status,json.dumps(prices),pub_ts))
        return cur.fetchone()[0]

def main():
    local=ZoneInfo("Europe/Warsaw"); now_local=datetime.now(local)
    day=os.environ.get("BUSINESS_DATE") or (now_local.date()+timedelta(days=1)).isoformat()
    db=os.environ.get("DATABASE_URL"); status=os.environ.get("FORECAST_STATUS","FROZEN").upper()
    if not db: raise SystemExit("Set DATABASE_URL")
    if status=="FROZEN" and now_local.time()>time(10,20):
        print(json.dumps({"event":"NO_FORECAST","day":day,"reason":"hard cutoff 10:20 Europe/Warsaw exceeded","timestamp":now_local.isoformat()}),flush=True); return
    if status not in ("DRAFT","FROZEN"): raise SystemExit("FORECAST_STATUS must be DRAFT or FROZEN")
    with psycopg.connect(db) as conn:
        sid,payload,retrieved=latest_snapshot(conn,day)
        prices=predict_24(payload); pub=publication_ts(payload)
        fid=persist(conn,day,sid,prices,status,pub); conn.commit()
        vals=[x["price"] for x in prices]
        print(json.dumps({"event":"forecast_saved","day":day,"forecast_id":fid,"snapshot_id":sid,
          "publication_ts":pub,"model_version":MODEL_VERSION,"status":status,
          "summary":{"avg":round(sum(vals)/24,2),"min":min(vals),"max":max(vals)},
          "prices":prices}),flush=True)

if __name__=="__main__": main()
