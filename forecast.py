import json, os, math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import psycopg

MODEL_VERSION="model-b-ridge-v1-20260918"
PSE_KEYS=["grid_demand_fcst","req_pow_res","surplus_cap_avail_tso","gen_surplus_avail_tso_above","avail_cap_gen_units_stor_prov","avail_cap_gen_units_stor_prov_tso","pred_gen_res_not_cov","fcst_gen_unit_stor_non_prov","fcst_wi_tot_gen","fcst_pv_tot_gen","planned_exchange","planned_restr_mwe_avail_shutdown","sum_unav_oper_cond","cap_market_obligation"]
MEAN=[16424.79752396166,279862181.0624334,1698.8320021299255,2998730.74613951,9112.771033013845,95510215.81709266,7413.938764643238,68488428.51876996,19700.203407880723,391223939.600639,19135.145899893505,369877265.2119276,10042.588391906284,114376297.1315229,6465.90887912673,47151582.54519436,2636.44262513312,10730957.607428115,2564.201411075612,18550785.01876997,-77.20507188498402,24970.289736421724,488.43949680511184,622020.6611421725,7076.206802449415,54611250.33859158,5029.482428115016,29750230.431842387,0,0,0,0,0,0,0.28434504792332266,0.07974214742338998,-0.11855081936278292,11301.358559637913,0.4980020334496558]
SCALE=[3176.1938478777265,108886503.06394997,335.70906255080314,1280152.6981362049,3530.9517013060995,70509888.08654994,3677.21912766839,61223273.84254906,1768.0286447803041,67420107.08641548,1929.626026388641,71300946.80799673,3677.3245059787787,74731903.14079408,2311.6238690635773,33385060.443426594,1944.2550994685075,14468841.861258302,3460.5860980775797,32856584.255232517,137.8755475479832,154720.2424219002,619.231393828625,1544419.3479991786,2130.3867319245637,32897069.566806592,2110.5773942513147,24959361.769722745,.7071067811865476,.7071067811865476,.7071067811865476,.7071067811865475,.7071067811865475,.7071067811865476,.4511019193539372,.7113517987730101,.6881609633909221,5485.167093476725,.31825579877859644]
COEF=[-25.493141212347144,162.15844288211923,72.4686852194582,-48.583471947841254,-6.271301182060344,-55.936657974513984,-12.64360080655471,194.28104775829536,37.511767370570716,-56.91917862468643,13.769174228118024,15.04532852801208,-113.27066143940479,-10.70479222189361,-81.62746460100958,-56.575318837478015,-8.540189435354453,5.535901772219949,-68.49075592156488,-15.370838521235287,4.321392093205263,1.7322055966456966,13.937870436997132,-5.479833072269646,-80.88122152587947,118.60704829614761,21.71589783112722,-35.430537064700886,-13.16645412621729,-18.011084609556796,-16.60632322002467,-10.763263940646091,13.086676817846193,-6.370926361331918,2.338728044161847,.8141931962948109,-33.408664804918395,31.367411262932585,-187.93680500826977]
INTERCEPT=466.3868443823215

def latest_snapshot(conn,day):
    with conn.cursor() as cur:
        cur.execute("SELECT id,payload,retrieved_at_utc FROM raw.snapshots WHERE business_date=%s ORDER BY retrieved_at_utc DESC LIMIT 1",(day,))
        row=cur.fetchone()
    if not row: raise RuntimeError(f"No snapshot for {day}")
    return row

def validate_snapshot(payload):
    pk5=payload.get("pk5l-wp",[])
    if len(pk5)!=24: raise RuntimeError(f"PK5L expected 24 rows, got {len(pk5)}")
    missing=sorted({k for r in pk5 for k in PSE_KEYS if r.get(k) is None})
    if missing: raise RuntimeError("PK5L missing model fields: "+",".join(missing))
    return pk5

def hour_no(row):
    p=str(row.get("period",""))
    try: return int(p.split("-")[0].strip())+1
    except: return int(str(row.get("plan_dtime"))[11:13])+1

def engineered(row,day,h):
    x=[float(row[k]) for k in PSE_KEYS]
    vals=[]
    for v in x: vals += [v,v*v]
    for k in (1,2,3):
        vals += [math.sin(2*math.pi*k*h/24),math.cos(2*math.pi*k*h/24)]
    d=datetime.strptime(day,"%Y-%m-%d")
    vals += [1.0 if d.weekday()>=5 else 0.0,math.sin(2*math.pi*d.month/12),math.cos(2*math.pi*d.month/12)]
    netload=x[0]-x[8]-x[9]-x[10]
    vals += [netload,x[3]/(x[0]+1.0)]
    return vals

def predict_24(payload,day):
    rows=sorted(validate_snapshot(payload),key=hour_no)
    out=[]
    for r in rows:
        h=hour_no(r); v=engineered(r,day,h)
        z=[(a-b)/s for a,b,s in zip(v,MEAN,SCALE)]
        price=INTERCEPT+sum(c*q for c,q in zip(COEF,z))
        out.append({"hour":h,"price":round(price,2)})
    if len(out)!=24 or [x["hour"] for x in out]!=list(range(1,25)):
        raise RuntimeError("Invalid hourly sequence")
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
            cur.execute("SELECT id FROM model.forecasts WHERE business_date=%s AND status='FROZEN' LIMIT 1",(day,))
            old=cur.fetchone()
            if old: return old[0]
        cur.execute("""INSERT INTO model.forecasts(business_date,snapshot_id,model_version,status,prices,publication_ts)
          VALUES(%s,%s,%s,%s,%s::jsonb,%s)
          ON CONFLICT(business_date,snapshot_id,model_version,status)
          DO UPDATE SET prices=EXCLUDED.prices,publication_ts=EXCLUDED.publication_ts,created_at=now() RETURNING id""",
          (day,snapshot_id,MODEL_VERSION,status,json.dumps(prices),pub_ts))
        return cur.fetchone()[0]

def main():
    day=os.environ.get("BUSINESS_DATE"); db=os.environ.get("DATABASE_URL"); status=os.environ.get("FORECAST_STATUS","DRAFT").upper()
    if not day or not db: raise SystemExit("Set BUSINESS_DATE and DATABASE_URL")
    if status not in ("DRAFT","FROZEN"): raise SystemExit("FORECAST_STATUS must be DRAFT or FROZEN")
    with psycopg.connect(db) as conn:
        sid,payload,retrieved=latest_snapshot(conn,day)
        prices=predict_24(payload,day); pub=publication_ts(payload)
        fid=persist(conn,day,sid,prices,status,pub); conn.commit()
        print(json.dumps({"event":"forecast_saved","day":day,"forecast_id":fid,"snapshot_id":sid,"publication_ts":pub,"model_version":MODEL_VERSION,"status":status,"prices":prices}),flush=True)
if __name__=="__main__": main()
