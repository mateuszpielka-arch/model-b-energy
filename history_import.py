import csv, glob, os
from datetime import datetime
import psycopg

def ensure_schema(conn):
    with conn.cursor() as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS history")
        c.execute("""CREATE TABLE IF NOT EXISTS history.hourly(
          delivery_date DATE NOT NULL,
          hour SMALLINT NOT NULL CHECK(hour BETWEEN 1 AND 24),
          jwcd_mw NUMERIC(12,4) NOT NULL,
          fix1_pln_mwh NUMERIC(12,4),
          mc_pln_mwh NUMERIC(12,4),
          source TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(delivery_date,hour)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS history_hourly_date_idx ON history.hourly(delivery_date)")
    conn.commit()

def import_seed(conn):
    files=sorted(glob.glob("history_seed/history_*.csv"))
    total=0
    with conn.cursor() as c:
        for fn in files:
            with open(fn,encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    c.execute("""INSERT INTO history.hourly(delivery_date,hour,jwcd_mw,fix1_pln_mwh,mc_pln_mwh,source)
                    VALUES(%s,%s,%s,%s,%s,'excel_v4_8')
                    ON CONFLICT(delivery_date,hour) DO UPDATE SET
                      jwcd_mw=EXCLUDED.jwcd_mw,
                      fix1_pln_mwh=COALESCE(EXCLUDED.fix1_pln_mwh,history.hourly.fix1_pln_mwh),
                      mc_pln_mwh=COALESCE(EXCLUDED.mc_pln_mwh,history.hourly.mc_pln_mwh),
                      updated_at=now()""",
                    (r["delivery_date"],int(float(r["hour"])),float(r["jwcd_mw"]),
                     float(r["fix1"]) if r.get("fix1") else None,
                     float(r["mc"]) if r.get("mc") else None))
                    total+=1
            conn.commit()
    return total

def hour_no(row):
    p=str(row.get("period",""))
    try: return int(p.split("-")[0].strip())+1
    except: return int(str(row.get("plan_dtime"))[11:13])+1

def sync_pk5(conn):
    with conn.cursor() as c:
        c.execute("SELECT business_date,payload FROM raw.snapshots ORDER BY business_date,retrieved_at_utc")
        snaps=c.fetchall()
        n=0
        for day,payload in snaps:
            rows=payload.get("pk5l-wp",[])
            if len(rows)!=24: continue
            for r in rows:
                v=r.get("fcst_gen_unit_stor_prov")
                if v is None: continue
                h=hour_no(r)
                c.execute("""INSERT INTO history.hourly(delivery_date,hour,jwcd_mw,source)
                  VALUES(%s,%s,%s,'pse_pk5')
                  ON CONFLICT(delivery_date,hour) DO UPDATE SET
                    jwcd_mw=EXCLUDED.jwcd_mw,source=CASE WHEN history.hourly.source='excel_v4_8' THEN history.hourly.source ELSE 'pse_pk5' END,updated_at=now()""",
                  (day,h,float(v))); n+=1
        conn.commit()
    return n

def sync_actuals(conn):
    with conn.cursor() as c:
        c.execute("""UPDATE history.hourly h SET fix1_pln_mwh=a.price_pln_mwh,updated_at=now()
          FROM actual.tge_fix1 a WHERE h.delivery_date=a.delivery_date AND h.hour=a.hour""")
        n=c.rowcount
        try:
            c.execute("""UPDATE history.hourly h SET mc_pln_mwh=a.price_pln_mwh,updated_at=now()
              FROM actual.tge_market_coupling a WHERE h.delivery_date=a.delivery_date AND h.hour=a.hour""")
            n+=c.rowcount
        except psycopg.errors.UndefinedTable:
            conn.rollback()
        conn.commit()
    return n

def main():
    db=os.environ["DATABASE_URL"]
    with psycopg.connect(db) as conn:
        ensure_schema(conn)
        seeded=import_seed(conn)
        pk5=sync_pk5(conn)
        actual=sync_actuals(conn)
        with conn.cursor() as c:
            c.execute("""SELECT count(*),count(DISTINCT delivery_date),min(delivery_date),max(delivery_date),
              count(*) FILTER(WHERE fix1_pln_mwh IS NOT NULL),count(*) FILTER(WHERE mc_pln_mwh IS NOT NULL)
              FROM history.hourly""")
            stats=c.fetchone()
    print({"seed_rows_processed":seeded,"pk5_rows_synced":pk5,"actual_rows_synced":actual,"stats":stats},flush=True)
if __name__=="__main__": main()
