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
from time import sleep
from urllib.parse import urlsplit
from zlib import error as ZlibError
from shadow_archive_batch import MAX_REFERENCE_BYTES, prepare_batch, read_reference_csv
from shadow_asof import cutoff_for_day
from shadow_scoring import _utc

MAX_HTML_BYTES = 8 * 1024 * 1024


def _unique_manifest_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate manifest field: {key}')
        result[key] = value
    return result


def _inside(root, filename):
    path=(root/filename).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Evidence file must exist inside manifest directory')
    return path


def _read_raw_evidence(path, entry):
    if path.suffix != '.gz':
        with path.open('rb') as stream:
            raw = stream.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise ValueError('HTML evidence exceeds 8 MiB')
        if 'bytes' in entry and (type(entry['bytes']) is not int or entry['bytes'] != len(raw)):
            raise ValueError('HTML evidence byte count mismatch')
        return raw
    size = entry.get('bytes')
    if type(size) is not int or not 0 < size <= MAX_HTML_BYTES:
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
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(), object_pairs_hook=_unique_manifest_object)
    if not isinstance(manifest, dict) or manifest.get('errors') != []:
        raise ValueError('Incomplete capture manifest: expected an explicit empty errors array')
    entries = manifest.get('entries')
    if not isinstance(entries, list) or not entries or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError('Incomplete capture manifest: expected a nonempty array of evidence objects')
    root = path.parent
    batches=[];dates=set()
    for e in entries:
        day = e.get('delivery_date')
        try:
            delivery = date.fromisoformat(day)
        except (TypeError, ValueError) as exc:
            raise ValueError('Manifest delivery date must use YYYY-MM-DD') from exc
        if day != delivery.isoformat() or delivery in (date.min, date.max):
            raise ValueError('Manifest delivery date must use YYYY-MM-DD within supported day boundaries')
        period_basis = e.get('period_basis')
        if period_basis == 'TGE_LABEL_ONLY':
            raise ValueError(f'TGE label-only evidence has no verified UTC mapping for {day}')
        if 'period_basis' in e and period_basis != 'EXPLICIT_UTC':
            raise ValueError(f'Unsupported Fix1 manifest period basis for {day}')
        session = (delivery - timedelta(days=1)).isoformat()
        if e.get('session_date') != session:
            raise ValueError(f'TGE session date must be {session} for delivery {day}')
        source_url = e.get('url')
        if not isinstance(source_url, str) or not urlsplit(source_url).query:
            raise ValueError(f'Historical TGE session URL required for delivery {day}')
        if day in dates: raise ValueError('Duplicate delivery date in manifest')
        dates.add(day)
        if e['http_status']!=200: raise ValueError('Unsuccessful source HTTP status')
        try:
            retrieved_at = _utc(datetime.fromisoformat(e.get('retrieved_at_utc')))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'Manifest requires an aware Fix1 retrieval timestamp for {day}') from exc
        if retrieved_at <= cutoff_for_day(delivery):
            raise ValueError(f'Fix1 retrieval must be after D-1 09:50 Europe/Warsaw for {day}')
        raw=_read_raw_evidence(_inside(root,e['raw_file']), e)
        with _inside(root, e['reference_file']).open('rb') as stream:
            reference = stream.read(MAX_REFERENCE_BYTES + 1)
        if len(reference) > MAX_REFERENCE_BYTES:
            raise ValueError('Fix1 reference CSV exceeds 64 KiB')
        if sha256(raw).hexdigest()!=e['raw_sha256'] or sha256(reference).hexdigest()!=e['reference_sha256']:
            raise ValueError('Evidence hash mismatch')
        batch=prepare_batch(raw=raw,day=day,source_url=source_url,
            retrieved_at=retrieved_at,
            reference_rows=read_reference_csv(reference, day, require_utc=period_basis == 'EXPLICIT_UTC'),
            reference_sha256=e['reference_sha256'])
        hours = e.get('hours')
        if type(hours) is not int or hours != len(batch.rows):
            raise ValueError(f'Manifest hour count does not match validated Fix1 prices for {day}')
        batches.append((batch,raw))
    return tuple(sorted(batches,key=lambda b:b[0].delivery_date))


def ingest_manifest(manifest_path, *, conn=None, retry_lock=False):
    # ALL file evidence is validated before opening any write transaction/DDL.
    batches = load_batches(manifest_path)
    if not retry_lock:
        return _ingest_batches(batches, conn)

    if conn is None:
        raise ValueError('Lock retries require an explicit isolated database connection')
    from psycopg.errors import LockNotAvailable
    from psycopg.pq import TransactionStatus
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Lock retries require an idle autocommit connection')
    for delay in (0.25, 0.5):
        try:
            return _ingest_batches(batches, conn)
        except LockNotAvailable:
            # The failed transaction has already rolled back; retry the whole manifest.
            sleep(delay)
    return _ingest_batches(batches, conn)


def _ingest_batches(batches, conn):
    if conn is not None:
        from shadow_postgres_archive import _guard, _has_delivery_quarantine, archive_batch, initialize
        _guard(conn)
    report=[]
    # Batches are sorted by delivery date, so overlapping imports acquire day
    # locks in the same order and retain them until the manifest commits.
    with (nullcontext() if conn is None else conn.transaction()):
        if conn is not None:
            initialize(conn)
        for batch,raw in batches:
            status = 'VALIDATED_DRY_RUN' if conn is None else archive_batch(conn, batch, raw)
            blocked = None
            if conn is not None:
                # A replay can match accepted prices even after a different capture was quarantined.
                blocked = status == 'QUARANTINE' or (
                    status == 'REPLAY' and _has_delivery_quarantine(conn, batch.delivery_date)
                )
            report.append(dict(day=batch.delivery_date.isoformat(),periods=len(batch.rows),status=status,
                               raw_sha256=batch.raw_sha256,source_url=batch.source_url,
                               scoring_blocked_by_quarantine=blocked))
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
    p.add_argument('--retry-lock', action='store_true', help='Retry the complete test write on PostgreSQL lock timeout')
    args=p.parse_args()
    if args.retry_lock and not args.write:
        p.error('--retry-lock requires --write')
    if args.write:
        if not args.dsn:p.error('--write requires explicit isolated-test --dsn')
        from shadow_postgres_archive import connect_test_database
        with connect_test_database(args.dsn) as conn:
            out = ingest_manifest(args.manifest, conn=conn, retry_lock=args.retry_lock)
    else:
        if args.dsn:p.error('--dsn without --write is rejected; dry-run needs no DB')
        out=ingest_manifest(args.manifest)
    print(json.dumps(out,indent=2))
