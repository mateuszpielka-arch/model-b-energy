"""Explicit pre-cutoff PK5 validation capture; no database or forecast writes.

Usage: python -m tools.capture_pk5_validation --day YYYY-MM-DD --output NEW_DIRECTORY
Only a complete standard-day response passing the SHADOW adapter is saved.
Transient request-open failures have at most three attempts, bounded by cutoff.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import gzip
from hashlib import sha256
from http.client import IncompleteRead
import json
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shadow_asof import cutoff_for_day
from shadow_pk5_snapshot import prepare_pk5_snapshot, pk5_standard_periods
from tools.capture_tge_validation import _write_new_evidence

MAX_PK5_BYTES = 8 * 1024 * 1024



def _open_with_retry(request, day):
    """Retry only transient connection/HTTP failures, never invalid payloads."""
    cutoff = cutoff_for_day(day)
    for attempt in range(3):
        if attempt and datetime.now(timezone.utc) > cutoff:
            raise ValueError('PK5 retry started after D-1 09:50 Europe/Warsaw')
        delay = 2 ** attempt
        try:
            return urlopen(request, timeout=35)
        except HTTPError as exc:
            exc.close()
            if attempt == 2 or exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get('Retry-After') if exc.headers else None
            if retry_after:
                # Unsupported HTTP-date or excessive waits are not retried.
                try:
                    seconds = retry_after.strip()
                    if not seconds.isascii() or not seconds.isdecimal():
                        raise ValueError('Retry-After requires nonnegative ASCII seconds')
                    delay = max(delay, int(seconds))
                except (TypeError, ValueError):
                    raise exc from None
                if delay > 5:
                    raise
        except (TimeoutError, URLError) as exc:
            if attempt == 2 or (isinstance(exc, URLError) and not isinstance(exc.reason, TimeoutError)):
                raise
        if datetime.now(timezone.utc) + timedelta(seconds=delay) > cutoff:
            raise ValueError('PK5 retry would start after D-1 09:50 Europe/Warsaw')
        sleep(delay)


def capture(day, directory):
    """Save validated original bytes and evidenced retrieval start/end, exclusively."""
    pk5_standard_periods(day)
    started = datetime.now(timezone.utc)
    if started > cutoff_for_day(day):
        raise ValueError('PK5 capture started after D-1 09:50 Europe/Warsaw')
    url = 'https://api.raporty.pse.pl/api/pk5l-wp?' + urlencode({
        '$filter': f"business_date eq '{day.isoformat()}'",
    })
    request = Request(url, headers={'User-Agent': 'model-b-energy-shadow-validation/1.0'})
    with _open_with_retry(request, day) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError('PK5 HTTP/source mismatch')
        length = response.length
        if isinstance(length, int) and length > MAX_PK5_BYTES:
            raise ValueError('PK5 response exceeds 8 MiB')
        raw = response.read(MAX_PK5_BYTES + 1)
        completed = datetime.now(timezone.utc)
        if isinstance(length, int) and len(raw) < length:
            raise IncompleteRead(raw, length - len(raw))
    if len(raw) > MAX_PK5_BYTES:
        raise ValueError('PK5 response exceeds 8 MiB')
    snapshot = prepare_pk5_snapshot(
        raw=raw, day=day.isoformat(), source_url=url,
        retrieved_at=started, retrieval_completed_at=completed,
    )
    metadata = dict(
        url=url, response_url=url, http_status=200, delivery_date=day.isoformat(),
        retrieved_at_utc=started.isoformat(), retrieval_completed_at_utc=completed.isoformat(),
        bytes=len(raw), raw_sha256=sha256(raw).hexdigest(),
        raw_file=f'pse_pk5_{day.isoformat()}.json.gz', rows=len(snapshot['rows']),
        snapshot_id=snapshot['id'],
    )
    directory = Path(directory)
    raw_path = directory / metadata['raw_file']
    _write_new_evidence(raw_path, gzip.compress(raw, mtime=0))
    try:
        _write_new_evidence(directory / 'capture.json',
                            (json.dumps(metadata, indent=2) + '\n').encode())
    except OSError:
        raw_path.unlink(missing_ok=True)
        raise
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', required=True, type=date.fromisoformat)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        args.output.mkdir(parents=True, exist_ok=False)
        metadata = capture(args.day, args.output)
    except (OSError, ValueError, TypeError, IncompleteRead) as exc:
        parser.error(str(exc))
    print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
