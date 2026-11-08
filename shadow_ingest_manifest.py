"""Validate captured TGE sessions before explicit writes to isolated test DB.

Default is dry-run; no network, DATABASE_URL, production writes, or scheduling.
"""
from collections import Counter
from contextlib import nullcontext
from datetime import date, datetime, timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit
from zlib import error as ZlibError
from shadow_archive_batch import prepare_batch, read_reference_csv


def _inside(root, filename):
    path=(root/filename).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Evidence file must exist inside manifest directory')
    return path


def _read_raw_evidence(path, entry):
    if path.suffix != '.gz':
        return path.read_bytes()
    size = entry.get('bytes')
    if type(size) is not int or not 0 < size <= 8 * 1024 * 1024:
        raise ValueError('Compressed HTML evidence requires a byte count up to 8 MiB')
    try:
        with gzip.open(path, 'rb') as stream:
            raw = stream.read(size + 1)
    except (OSError, EOFError, ZlibError) as exc:
        raise ValueError('Invalid gzip HTML evidence') from exc
    if len(raw) != size:
        raise ValueError('Uncompressed HTML evidence byte count mismatch')
    return raw


def load_batches(manifest_path):
    path=Path(manifest_path);manifest=json.loads(path.read_text());root=path.parent
    if manifest.get('errors') or not manifest.get('entries'):
        raise ValueError('Incomplete capture manifest')
    batches=[];dates=set()
    for e in manifest['entries']:
        day=e['delivery_date']
        if e.get('period_basis') == 'TGE_LABEL_ONLY':
            raise ValueError(f'TGE label-only evidence has no verified UTC mapping for {day}')
        session = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        if e.get('session_date') != session:
            raise ValueError(f'TGE session date must be {session} for delivery {day}')
        source_url = e.get('url')
        if not isinstance(source_url, str) or not urlsplit(source_url).query:
            raise ValueError(f'Historical TGE session URL required for delivery {day}')
        if day in dates: raise ValueError('Duplicate delivery date in manifest')
        dates.add(day)
        if e['http_status']!=200: raise ValueError('Unsuccessful source HTTP status')
        raw=_read_raw_evidence(_inside(root,e['raw_file']), e)
        reference=_inside(root,e['reference_file']).read_bytes()
        if sha256(raw).hexdigest()!=e['raw_sha256'] or sha256(reference).hexdigest()!=e['reference_sha256']:
            raise ValueError('Evidence hash mismatch')
        batch=prepare_batch(raw=raw,day=day,source_url=source_url,
            retrieved_at=datetime.fromisoformat(e['retrieved_at_utc']),
            reference_rows=read_reference_csv(reference, day),
            reference_sha256=e['reference_sha256'])
        hours = e.get('hours')
        if type(hours) is not int or hours != len(batch.rows):
            raise ValueError(f'Manifest hour count does not match validated Fix1 prices for {day}')
        batches.append((batch,raw))
    return tuple(sorted(batches,key=lambda b:b[0].delivery_date))


def ingest_manifest(manifest_path, *, conn=None):
    # ALL file evidence is validated before opening any write transaction/DDL.
    batches=load_batches(manifest_path)
    if conn is not None:
        from shadow_postgres_archive import initialize,archive_batch
        initialize(conn)
    report=[]
    # Batches are sorted by delivery date, so overlapping imports acquire day
    # locks in the same order and retain them until the manifest commits.
    with (nullcontext() if conn is None else conn.transaction()):
        for batch,raw in batches:
            status='VALIDATED_DRY_RUN' if conn is None else archive_batch(conn,batch,raw)
            report.append(dict(day=batch.delivery_date.isoformat(),periods=len(batch.rows),status=status,
                               raw_sha256=batch.raw_sha256,source_url=batch.source_url))
    return dict(
        dry_run=conn is None,
        days=len(report),
        periods=sum(row['periods'] for row in report),
        days_by_status=dict(sorted(Counter(row['status'] for row in report).items())),
        inserted_periods=sum(row['periods'] for row in report if row['status'] == 'INSERT'),
        results=report,
    )


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('manifest');p.add_argument('--write',action='store_true');p.add_argument('--dsn')
    args=p.parse_args()
    if args.write:
        if not args.dsn:p.error('--write requires explicit isolated-test --dsn')
        from shadow_postgres_archive import connect_test_database
        with connect_test_database(args.dsn) as conn:out=ingest_manifest(args.manifest,conn=conn)
    else:
        if args.dsn:p.error('--dsn without --write is rejected; dry-run needs no DB')
        out=ingest_manifest(args.manifest)
    print(json.dumps(out,indent=2))
