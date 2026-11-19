"""Join evidence-checked standard-day PK5 and Fix1 into offline audit CSV rows.

No I/O or database access. HTTP provenance is caller-owned; consistency checks
do not authenticate an external source. No historic timestamps are inferred.
"""
from hashlib import sha256

from shadow_archive_batch import prepare_batch, read_reference_csv, revalidate_batch
from shadow_pk5_snapshot import prepare_pk5_snapshot


def prepare_history_rows(*, pk5_raw, pk5_source_url, retrieved_at, retrieval_completed_at,
                         actual_batch, actual_raw, actual_reference):
    """Require original source bytes and a UTC reference before marking rows ready."""
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
    snapshot = prepare_pk5_snapshot(
        raw=pk5_raw, day=batch.delivery_date.isoformat(), source_url=pk5_source_url,
        retrieved_at=retrieved_at, retrieval_completed_at=retrieval_completed_at,
    )
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
