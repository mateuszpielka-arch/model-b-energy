import json, os
from datetime import datetime, timezone
import psycopg


def latest_snapshot(conn, day):
    with conn.cursor() as cur:
        cur.execute("""SELECT id, payload FROM raw.snapshots
                     WHERE business_date=%s ORDER BY retrieved_at_utc DESC LIMIT 1""", (day,))
        row=cur.fetchone()
    if not row:
        raise RuntimeError(f"No snapshot for {day}")
    return row[0], row[1]


def validate_snapshot(payload):
    pk5=payload.get("pk5l-wp", [])
    if len(pk5) != 24:
        raise RuntimeError(f"PK5L expected 24 rows, got {len(pk5)}")
    entsoe=payload.get("entsoe", {})
    for key in ("de_lu_day_ahead_price", "de_lu_load_forecast"):
        if entsoe.get(key, {}).get("status") != 200:
            raise RuntimeError(f"ENTSO-E {key} unavailable")
    return pk5


def predict_24(payload):
    # Intentionally no fabricated forecast. A trained Model B artifact/parameters
    # must be supplied before this function is allowed to emit market prices.
    raise RuntimeError("MODEL_B_NOT_TRAINED: historical training dataset/model artifact required")


def persist(conn, day, snapshot_id, prices, status="DRAFT"):
    if len(prices) != 24:
        raise RuntimeError("Forecast must contain exactly 24 hourly prices")
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS model")
        cur.execute("""CREATE TABLE IF NOT EXISTS model.forecasts (
            id BIGSERIAL PRIMARY KEY,
            business_date DATE NOT NULL,
            snapshot_id BIGINT NOT NULL REFERENCES raw.snapshots(id),
            model_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('DRAFT','FROZEN')),
            prices JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (business_date, snapshot_id, model_version, status)
        )""")
        cur.execute("""INSERT INTO model.forecasts
            (business_date,snapshot_id,model_version,status,prices)
            VALUES (%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (business_date,snapshot_id,model_version,status)
            DO UPDATE SET prices=EXCLUDED.prices, created_at=now()
            RETURNING id""",
            (day,snapshot_id,"model-b-v1",status,json.dumps(prices)))
        return cur.fetchone()[0]


def main():
    day=os.environ.get("BUSINESS_DATE")
    db=os.environ.get("DATABASE_URL")
    if not day or not db:
        raise SystemExit("Set BUSINESS_DATE and DATABASE_URL")
    with psycopg.connect(db) as conn:
        snapshot_id,payload=latest_snapshot(conn,day)
        validate_snapshot(payload)
        print(json.dumps({"event":"forecast_input_valid","day":day,"snapshot_id":snapshot_id}), flush=True)
        prices=predict_24(payload)
        forecast_id=persist(conn,day,snapshot_id,prices,os.environ.get("FORECAST_STATUS","DRAFT"))
        conn.commit()
        print(json.dumps({"event":"forecast_saved","day":day,"forecast_id":forecast_id,"hours":24}), flush=True)

if __name__ == "__main__":
    main()
