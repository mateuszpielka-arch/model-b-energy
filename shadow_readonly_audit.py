"""Explicit read-only audit of existing schemas; no migration or model execution.

No ambient DATABASE_URL. Never exports credentials or complete raw payloads.
"""
from datetime import datetime,timezone
from collections import Counter
from shadow_asof import cutoff_for_day
from shadow_scoring import expected_periods


def connect_readonly(dsn):
    import psycopg
    return psycopg.connect(dsn,autocommit=True,
        options='-c default_transaction_read_only=on -c statement_timeout=30000')


def audit_connection(conn):
    if not conn.autocommit: raise ValueError('Dedicated autocommit connection required')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        if conn.execute('SHOW transaction_read_only').fetchone()[0]!='on':
            raise ValueError('Database did not enforce READ ONLY')
        present={name:conn.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0] is not None
                 for name in ('raw.snapshots','actual.tge_fix1','history.hourly')}
        snapshots=[];actuals=[]
        if present['raw.snapshots']:
            rows=conn.execute('SELECT id,business_date,retrieved_at_utc,payload FROM raw.snapshots ORDER BY business_date,retrieved_at_utc,id').fetchall()
            for sid,day,retrieved,payload in rows:
                values=payload.get('pk5l-wp',[]) if isinstance(payload,dict) else None
                if not isinstance(values,list):
                    snapshots.append(dict(snapshot_id=sid,day=day.isoformat(),error='INVALID_PK5_ARRAY'));continue
                labels=[str(r.get('period','')) for r in values if isinstance(r,dict)]
                counts=Counter(labels)
                cutoff = cutoff_for_day(day)
                availability = (
                    'RETRIEVAL_STARTED_AFTER_CUTOFF' if retrieved > cutoff
                    else 'UNPROVEN_NO_VERIFIED_COMPLETION_TIMESTAMP'
                )
                snapshots.append(dict(snapshot_id=sid,day=day.isoformat(),expected_periods=len(expected_periods(day)),
                    rows=len(values),distinct_raw_labels=len(counts),repeated_raw_labels=sum(v-1 for v in counts.values()),
                    jwcd_nonnull=sum(isinstance(r,dict) and r.get('fcst_gen_unit_stor_prov') is not None for r in values),
                    publication_nonnull=sum(isinstance(r,dict) and bool(r.get('publication_ts')) for r in values),
                    retrieved_at_utc=retrieved.isoformat(),
                    cutoff_utc=cutoff.isoformat(),
                    availability_status=availability,
                    model_ready=False))
        if present['actual.tge_fix1']:
            rows=conn.execute('''SELECT delivery_date,count(*),
                array_agg(DISTINCT hour) FILTER (WHERE hour IS NOT NULL),
                count(*) FILTER (WHERE price_pln_mwh IN (60,2026))
                FROM actual.tge_fix1 GROUP BY delivery_date ORDER BY delivery_date''').fetchall()
            for day,n,hours,suspect in rows:
                expected=len(expected_periods(day))
                observed = set(hours or [])
                required = set(range(1, expected + 1))
                actuals.append(dict(day=day.isoformat(),rows=n,distinct_legacy_hours=len(observed),
                    expected_periods=expected,structurally_complete=(n==expected and observed==required),
                    missing_legacy_hours=sorted(required-observed),
                    unexpected_legacy_hours=sorted(observed-required),
                    prices_equal_60_or_2026=suspect,
                    quality_status='UNVERIFIED_REQUIRES_SOURCE_RECONCILIATION'))
        return dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),transaction_read_only=True,
                    tables=present,snapshots=snapshots,actual_days=actuals,
                    note='60/2026 are diagnostics, not proof of invalid price. Legacy retrieval time is not completion evidence.')


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dsn-file',required=True,help='Private local file containing authorized read-only connection DSN')
    args=p.parse_args()
    from pathlib import Path
    dsn=Path(args.dsn_file).read_text().strip()
    with connect_readonly(dsn) as conn: result=audit_connection(conn)
    print(json.dumps(result,indent=2))
