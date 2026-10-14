"""Read-only PostgreSQL completeness audit for an isolated Railway SHADOW job.
DO NOT run on production forecast service; requires injected DATABASE_URL.
No INSERT/UPDATE/DELETE/DDL. Prints only aggregate counts, not private values.
"""
import os, json
from datetime import date, timedelta
import psycopg

def main():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("""
                SELECT count(*) AS rows, count(DISTINCT delivery_date) AS days,
                       min(delivery_date), max(delivery_date),
                       count(*) FILTER (WHERE jwcd_mw IS NOT NULL AND fix1_pln_mwh IS NOT NULL) AS paired_rows
                FROM history.hourly
            """)
            print("history",cur.fetchone(),flush=True)
            cur.execute("""
                WITH per_day AS (
                  SELECT delivery_date, count(*) n, count(DISTINCT hour) hours,
                         count(*) FILTER (WHERE jwcd_mw IS NOT NULL AND fix1_pln_mwh IS NOT NULL) paired
                  FROM history.hourly
                  WHERE delivery_date >= CURRENT_DATE - 100
                  GROUP BY delivery_date
                )
                SELECT min(delivery_date), max(delivery_date), count(*),
                       count(*) FILTER (WHERE n=24 AND hours=24 AND paired=24),
                       count(*) FILTER (WHERE n<>24 OR hours<>24 OR paired<>24)
                FROM per_day
            """)
            print("recent_100d_completeness",cur.fetchone(),flush=True)
            cur.execute("""
                SELECT business_date, count(*) snapshots,
                       min(retrieved_at_utc), max(retrieved_at_utc)
                FROM raw.snapshots WHERE business_date >= CURRENT_DATE - 7
                GROUP BY business_date ORDER BY business_date DESC
            """)
            print("recent_snapshots",cur.fetchall(),flush=True)
            cur.execute("""
                SELECT business_date, model_version, status, count(*)
                FROM model.forecasts WHERE business_date >= CURRENT_DATE - 7
                GROUP BY business_date, model_version, status
                ORDER BY business_date DESC
            """)
            print("recent_forecasts",cur.fetchall(),flush=True)
            conn.rollback()

if __name__ == "__main__": main()
