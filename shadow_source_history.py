"""Join evidence-checked standard-day PK5 and Fix1 into offline audit CSV rows.

The preparation function performs no I/O. The explicit CLI reads local files and
exports CSV to stdout, without network or database access. HTTP provenance remains
caller-owned; no historic timestamps are inferred.
"""
from collections.abc import Mapping
from hashlib import sha256

from shadow_archive_batch import prepare_batch, read_reference_csv, revalidate_batch
from shadow_pk5_snapshot import prepare_pk5_snapshot
from shadow_asof import select_snapshot


def prepare_history_rows(*, pk5_raw, pk5_source_url, retrieved_at, retrieval_completed_at,
                         actual_batch, actual_raw, actual_reference):
    """Require original source bytes and a UTC reference before marking rows ready."""
    return prepare_history_rows_from_captures(
        pk5_captures=[dict(raw=pk5_raw, source_url=pk5_source_url,
                           retrieved_at=retrieved_at,
                           retrieval_completed_at=retrieval_completed_at)],
        actual_batch=actual_batch, actual_raw=actual_raw, actual_reference=actual_reference,
    )


def prepare_history_rows_from_captures(*, pk5_captures, actual_batch, actual_raw,
                                      actual_reference):
    """Choose one complete capture using the existing publication/conflict gate.

    Each capture supplies raw bytes, source_url and aware retrieval start/end.
    All supplied captures must pass source validation and their own cutoff;
    invalid or late evidence is never silently ignored. No periods are blended.
    """
    fields = {'raw', 'source_url', 'retrieved_at', 'retrieval_completed_at'}
    if not isinstance(pk5_captures, (list, tuple)) or not pk5_captures:
        raise ValueError('Nonempty list or tuple of PK5 captures required')
    if any(not isinstance(capture, Mapping) or set(capture) != fields
           for capture in pk5_captures):
        raise ValueError('PK5 capture requires raw, source_url and retrieval start/end only')
    batch = revalidate_batch(actual_batch, actual_raw)
    if (not isinstance(actual_reference, bytes)
            or sha256(actual_reference).hexdigest() != batch.reference_sha256):
        raise ValueError('History source Fix1 reference hash mismatch')
    # Recheck the reference against raw prices, including its explicit UTC keys.
    batch = prepare_batch(
        raw=actual_raw, day=batch.delivery_date.isoformat(), source_url=batch.source_url,
        retrieved_at=batch.retrieved_at, reference_sha256=batch.reference_sha256,
        reference_rows=read_reference_csv(
            actual_reference, batch.delivery_date.isoformat(), require_utc=True,
        ),
    )
    snapshots = [
        prepare_pk5_snapshot(day=batch.delivery_date.isoformat(), **capture)
        for capture in pk5_captures
    ]
    selected = select_snapshot(batch.delivery_date, snapshots)
    if selected.snapshot_id is None:
        raise ValueError('PK5 history selection rejected: ' +
                         ', '.join(reason for _, reason in selected.rejected))
    snapshot = next(snapshot for snapshot in snapshots if snapshot['id'] == selected.snapshot_id)
    actual = dict(batch.rows)
    version = ':'.join((batch.delivery_date.isoformat(), batch.raw_sha256,
                        batch.reference_sha256, batch.parser_version))
    return tuple(
        dict(
            delivery_date=batch.delivery_date.isoformat(), hour=f'H{index:02d}',
            period_start_utc=row['period_start_utc'].isoformat(),
            jwcd_mw=str(row['jwcd_mw']), fix1_pln_mwh=str(actual[row['period_start_utc']]),
            actual_verified='true', actual_version=version,
            actual_available_at=batch.retrieved_at.isoformat(),
            jwcd_snapshot_id=snapshot['id'], publication_ts=row['publication_ts'].isoformat(),
            retrieved_at=snapshot['retrieved_at'].isoformat(),
            retrieval_completed_at=snapshot['retrieval_completed_at'].isoformat(),
        )
        for index, row in enumerate(sorted(snapshot['rows'], key=lambda row: row['period_start_utc']), 1)
    )


def main():
    """Explicit local conversion; validate everything before producing any CSV."""
    import argparse
    import csv
    import gzip
    import io
    import json
    from pathlib import Path
    import sys
    from zlib import error as ZlibError

    from shadow_ingest_manifest import (
        MAX_MANIFEST_BYTES, MAX_REFERENCE_BYTES, _inside, _unique_manifest_object, load_batches,
    )
    from shadow_pk5_snapshot import _unique_object, parse_capture_timestamp

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pk5-capture', required=True, type=Path, action='append',
                        help='Saved PK5 capture.json alongside original JSON gzip; repeat for candidate captures')
    parser.add_argument('actual_manifest', type=Path, help='One-day verified Fixing I manifest')
    args = parser.parse_args()
    try:
        batches = load_batches(args.actual_manifest)
        if len(batches) != 1:
            raise ValueError('Exactly one Fixing I delivery day required')
        batch, actual_raw = batches[0]
        captures = []
        for capture_path in args.pk5_capture:
            with capture_path.open('rb') as stream:
                metadata_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(metadata_bytes) > MAX_MANIFEST_BYTES:
                raise ValueError('PK5 capture metadata exceeds 1 MiB')
            metadata = json.loads(metadata_bytes, object_pairs_hook=_unique_object)
            if not isinstance(metadata, dict):
                raise ValueError('PK5 capture metadata must be an object')
            filename = metadata.get('raw_file')
            if (not isinstance(filename, str) or Path(filename).name != filename
                    or not filename.endswith('.json.gz')):
                raise ValueError('PK5 capture requires a local JSON gzip filename')
            if (type(metadata.get('http_status')) is not int or metadata['http_status'] != 200
                    or metadata.get('response_url') != metadata.get('url')
                    or type(metadata.get('rows')) is not int or metadata['rows'] != 24):
                raise ValueError('PK5 capture HTTP/source/row metadata mismatch')
            with gzip.open(_inside(capture_path.parent, filename), 'rb') as stream:
                pk5_raw = stream.read(8 * 1024 * 1024 + 1)
            if (len(pk5_raw) > 8 * 1024 * 1024 or type(metadata.get('bytes')) is not int
                    or metadata['bytes'] != len(pk5_raw)
                    or metadata.get('raw_sha256') != sha256(pk5_raw).hexdigest()):
                raise ValueError('PK5 capture raw size or SHA-256 mismatch')
            if metadata.get('delivery_date') != batch.delivery_date.isoformat():
                raise ValueError('PK5 capture delivery differs from Fixing I')
            captures.append(dict(
                raw=pk5_raw, source_url=metadata['url'],
                retrieved_at=parse_capture_timestamp(metadata['retrieved_at_utc']),
                retrieval_completed_at=parse_capture_timestamp(metadata['retrieval_completed_at_utc']),
            ))
        with args.actual_manifest.open('rb') as stream:
            manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError('Fix1 capture manifest exceeds 1 MiB')
        manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_manifest_object)
        entries = manifest.get('entries') if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise ValueError('Exactly one Fixing I evidence entry required')
        reference_path = _inside(args.actual_manifest.parent, entries[0]['reference_file'])
        with reference_path.open('rb') as stream:
            reference = stream.read(MAX_REFERENCE_BYTES + 1)
        rows = prepare_history_rows_from_captures(
            pk5_captures=captures,
            actual_batch=batch, actual_raw=actual_raw, actual_reference=reference,
        )
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=rows[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    sys.stdout.write(output.getvalue())


if __name__ == '__main__':
    main()
