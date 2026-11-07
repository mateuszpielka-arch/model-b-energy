"""Validate captured TGE sessions before explicit writes to isolated test DB.

Default is dry-run; no network, DATABASE_URL, production writes, or scheduling.
"""
import csv
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
from zlib import error as ZlibError
from shadow_archive_batch import prepare_batch


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
        if day in dates: raise ValueError('Duplicate delivery date in manifest')
        dates.add(day)
        if e['http_status']!=200: raise ValueError('Unsuccessful source HTTP status')
        raw=_read_raw_evidence(_inside(root,e['raw_file']), e)
        reference=_inside(root,e['reference_file']).read_bytes()
        if sha256(raw).hexdigest()!=e['raw_sha256'] or sha256(reference).hexdigest()!=e['reference_sha256']:
            raise ValueError('Evidence hash mismatch')
        rows=list(csv.DictReader(line for line in reference.decode('utf-8-sig').splitlines() if not line.startswith('#')))
        if any(r['delivery_date']!=day for r in rows): raise ValueError('Reference delivery date mismatch')
        batch=prepare_batch(raw=raw,day=day,source_url=e['url'],
            retrieved_at=datetime.fromisoformat(e['retrieved_at_utc']),
            reference_rows=[(r['hour'],r['price_pln_mwh']) for r in rows],
            reference_sha256=e['reference_sha256'])
        batches.append((batch,raw))
    return tuple(sorted(batches,key=lambda b:b[0].delivery_date))


def ingest_manifest(manifest_path, *, conn=None):
    # ALL file evidence is validated before opening any write transaction/DDL.
    batches=load_batches(manifest_path)
    if conn is not None:
        from shadow_postgres_archive import initialize,archive_batch
        initialize(conn)
    report=[]
    for batch,raw in batches:
        status='VALIDATED_DRY_RUN' if conn is None else archive_batch(conn,batch,raw)
        report.append(dict(day=batch.delivery_date.isoformat(),periods=len(batch.rows),status=status,
                           raw_sha256=batch.raw_sha256,source_url=batch.source_url))
    return dict(dry_run=conn is None,days=len(report),periods=sum(r['periods'] for r in report),results=report)


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
