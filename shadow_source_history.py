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
from shadow_history_audit import audit_rows


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
    return prepare_history_report_from_captures(
        pk5_captures=pk5_captures, actual_batch=actual_batch,
        actual_raw=actual_raw, actual_reference=actual_reference,
    )['rows']


def prepare_history_report_from_captures(*, pk5_captures, actual_batch, actual_raw,
                                        actual_reference):
    """Return rows and JSON-safe evidence of whole-capture selection; no I/O.

    Hashes and consistency checks do not independently authenticate capture times.
    Superseded observations remain visible in the report, without blending rows.
    Readiness describes only the exported rows, not any external history archive.
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
    rows = tuple(
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
    return dict(
        delivery_date=batch.delivery_date.isoformat(), periods=len(rows),
        cutoff_utc=selected.cutoff_utc.isoformat(), selected_snapshot_id=selected.snapshot_id,
        pk5_captures=[
            dict(
                snapshot_id=candidate['id'], raw_sha256=candidate['raw_sha256'],
                source_url=candidate['source_url'],
                retrieved_at_utc=candidate['retrieved_at'].isoformat(),
                retrieval_completed_at_utc=candidate['retrieval_completed_at'].isoformat(),
                latest_publication_ts_utc=max(
                    row['publication_ts'] for row in candidate['rows']
                ).isoformat(),
                periods=len(candidate['rows']),
                selected=candidate['id'] == selected.snapshot_id,
            )
            for candidate in sorted(snapshots, key=lambda candidate: candidate['id'])
        ],
        rejected_captures=[
            dict(snapshot_id=identifier, reason=reason)
            for identifier, reason in sorted(selected.rejected)
        ],
        fix1_evidence=dict(
            raw_sha256=batch.raw_sha256, reference_sha256=batch.reference_sha256,
            parser_version=batch.parser_version, source_url=batch.source_url,
            retrieved_at_utc=batch.retrieved_at.isoformat(), actual_version=version,
        ),
        rows=rows, history_audit=audit_rows(rows),
    )



def prepare_history_collection(*, days):
    """Validate whole source packages before returning a sorted multi-day history.

    No I/O, partial result, timestamp inference or external archive mutation.
    Per-day selection evidence is retained; readiness covers the combined rows.
    """
    fields = {'pk5_captures', 'actual_batch', 'actual_raw', 'actual_reference'}
    if not isinstance(days, (list, tuple)) or not days:
        raise ValueError('Nonempty list or tuple of source day packages required')
    if any(not isinstance(package, Mapping) or set(package) != fields for package in days):
        raise ValueError('Source day package requires captures and Fix1 batch/raw/reference only')
    reports = [prepare_history_report_from_captures(**package) for package in days]
    labels = [report['delivery_date'] for report in reports]
    if len(set(labels)) != len(labels):
        raise ValueError('Duplicate delivery date in source history collection')
    reports.sort(key=lambda report: report['delivery_date'])
    rows = tuple(row for report in reports for row in report['rows'])
    return dict(days=reports, periods=len(rows), rows=rows, history_audit=audit_rows(rows))


def load_pk5_capture(capture_path, *, day):
    """Read and validate one saved PK5 capture without requiring Fix1 evidence.

    Only explicit local files are read. Capture timestamps remain caller-owned;
    this validates their consistency and cutoff, not their authenticity.
    """
    from datetime import date
    import gzip
    import json
    from pathlib import Path
    from shadow_ingest_manifest import MAX_MANIFEST_BYTES, _inside
    from shadow_pk5_snapshot import _unique_object, parse_capture_timestamp

    if type(day) is not date or day in (date.min, date.max):
        raise ValueError('Supported delivery date required')
    capture_path = Path(capture_path)
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
    if metadata.get('delivery_date') != day.isoformat():
        raise ValueError('PK5 capture delivery differs from Fixing I')
    capture = dict(
        raw=pk5_raw, source_url=metadata['url'],
        retrieved_at=parse_capture_timestamp(metadata['retrieved_at_utc']),
        retrieval_completed_at=parse_capture_timestamp(metadata['retrieval_completed_at_utc']),
    )
    # Always validate publication/retrieval/cutoff, including legacy metadata.
    snapshot = prepare_pk5_snapshot(day=day.isoformat(), **capture)
    if 'snapshot_id' in metadata and metadata['snapshot_id'] != snapshot['id']:
        raise ValueError('PK5 capture snapshot_id mismatch')
    return capture


def load_history_day(actual_manifest, pk5_capture_paths):
    """Read one explicit local source package using the CLI evidence checks."""
    import json
    from pathlib import Path
    from shadow_ingest_manifest import (
        MAX_MANIFEST_BYTES, MAX_REFERENCE_BYTES, _inside, _unique_manifest_object, load_batches,
    )

    actual_manifest = Path(actual_manifest)
    pk5_capture_paths = [Path(path) for path in pk5_capture_paths]
    if not pk5_capture_paths:
        raise ValueError('At least one PK5 capture path required')
    batches = load_batches(actual_manifest)
    if len(batches) != 1:
        raise ValueError('Exactly one Fixing I delivery day required')
    batch, actual_raw = batches[0]
    captures = [load_pk5_capture(path, day=batch.delivery_date)
                for path in pk5_capture_paths]
    with actual_manifest.open('rb') as stream:
        manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError('Fix1 capture manifest exceeds 1 MiB')
    manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_manifest_object)
    entries = manifest.get('entries') if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError('Exactly one Fixing I evidence entry required')
    reference_path = _inside(actual_manifest.parent, entries[0]['reference_file'])
    with reference_path.open('rb') as stream:
        reference = stream.read(MAX_REFERENCE_BYTES + 1)
    return dict(pk5_captures=captures, actual_batch=batch,
                actual_raw=actual_raw, actual_reference=reference)


def main():
    """Explicit local conversion; validate everything before producing any CSV."""
    import argparse
    import csv
    import io
    import json
    from pathlib import Path
    import sys
    from zlib import error as ZlibError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pk5-capture', required=True, type=Path, action='append',
                        help='Saved PK5 capture.json alongside original JSON gzip; repeat for candidate captures')
    parser.add_argument('actual_manifest', type=Path, help='One-day verified Fixing I manifest')
    parser.add_argument('--format', choices=('csv', 'json'), default='csv',
                        help='CSV rows (default) or rows with capture-selection evidence as JSON')
    args = parser.parse_args()
    try:
        report = prepare_history_report_from_captures(
            **load_history_day(args.actual_manifest, args.pk5_capture),
        )
    except (OSError, EOFError, ZlibError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    if args.format == 'json':
        sys.stdout.write(json.dumps(report, indent=2) + '\n')
        return
    rows = report['rows']
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=rows[0], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    sys.stdout.write(output.getvalue())


if __name__ == '__main__':
    main()
