import json, os, sys
from datetime import datetime, timezone
import requests

BASE="https://api.raporty.pse.pl/api"
HEADERS={"accept":"application/json","User-Agent":"model-b-energy/0.2"}

def fetch(endpoint, business_date):
    url=f"{BASE}/{endpoint}"
    params={"$filter":f"business_date eq '{business_date}'"}
    rows=[]; pages=0
    while url:
        r=requests.get(url,params=params if pages==0 else None,headers=HEADERS,timeout=30)
        print(json.dumps({"event":"http","endpoint":endpoint,"status":r.status_code,"url":r.url},ensure_ascii=False),flush=True)
        r.raise_for_status()
        data=r.json(); rows.extend(data.get("value",[]))
        url=data.get("nextLink") or data.get("@odata.nextLink")
        params=None; pages+=1
        if pages>100: raise RuntimeError("pagination guard")
    return rows

def main():
    day=os.environ.get("BUSINESS_DATE") or (sys.argv[1] if len(sys.argv)>1 else None)
    if not day: raise SystemExit("Set BUSINESS_DATE=YYYY-MM-DD")
    retrieved=datetime.now(timezone.utc).isoformat()
    out={"business_date":day,"retrieved_at_utc":retrieved}
    for ep in ("pk5l-wp","unav-pk5l"):
        rows=fetch(ep,day); out[ep]=rows
        pubs=sorted({str(x.get("publication_ts")) for x in rows if x.get("publication_ts")})
        print(json.dumps({"event":"summary","endpoint":ep,"business_date":day,"records":len(rows),"publication_ts":pubs},ensure_ascii=False),flush=True)
        if ep=="pk5l-wp":
            for x in rows:
                print(json.dumps({"event":"pk5l_row","period":x.get("period"),"plan_dtime":x.get("plan_dtime"),"grid_demand_fcst":x.get("grid_demand_fcst"),"req_pow_res":x.get("req_pow_res"),"surplus_cap_avail_tso":x.get("surplus_cap_avail_tso"),"gen_surplus_avail_tso_above":x.get("gen_surplus_avail_tso_above"),"avail_cap_gen_units_stor_prov":x.get("avail_cap_gen_units_stor_prov"),"avail_cap_gen_units_stor_prov_tso":x.get("avail_cap_gen_units_stor_prov_tso"),"fcst_gen_unit_stor_prov":x.get("fcst_gen_unit_stor_prov"),"fcst_gen_unit_stor_non_prov":x.get("fcst_gen_unit_stor_non_prov"),"fcst_wi_tot_gen":x.get("fcst_wi_tot_gen"),"fcst_pv_tot_gen":x.get("fcst_pv_tot_gen"),"planned_exchange":x.get("planned_exchange"),"publication_ts":x.get("publication_ts")},ensure_ascii=False),flush=True)
    os.makedirs("data",exist_ok=True)
    path=f"data/pse_{day}.json"
    with open(path,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({"event":"saved","path":path}),flush=True)

if __name__=="__main__": main()
