"""Explicit read-only TGE validation capture; never archives production prices.

Optional lxml dependency provides extraction independent of tge_parser_safe.
No model/parser output is used to create the reference values.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MAX_HTML_BYTES = 8 * 1024 * 1024


def extract_reference(raw, day, *, autumn_25h=False):
    """Read Fixing I prices, optionally preserving autumn labels without mapping to UTC."""
    from lxml import html
    expected = [f'H{hour:02d}' for hour in range(1, 25)]
    if autumn_25h:
        expected.insert(2, 'H02a')
    tree=html.fromstring(raw)
    tables=tree.xpath('//table[@id="rdn"]')
    if len(tables)!=1: raise ValueError('Expected one official RDN table')
    headers=tables[0].xpath('./thead/tr')
    if len(headers)!=2: raise ValueError('Header layout changed')
    groups=headers[0].xpath('./th')
    if len(groups)<3 or ' '.join(groups[2].text_content().split())!='Fixing I':
        raise ValueError('Third column group is not Fixing I')
    fixing_groups = [group for group in groups
                     if ' '.join(group.text_content().split()) == 'Fixing I']
    if len(fixing_groups) != 1 or fixing_groups[0].get('colspan') != '2':
        raise ValueError('Expected one two-column Fixing I group')
    columns = headers[1].xpath('./th')
    if any(group.get('colspan', '1') != '1' for group in groups[:2]) or any(
        cell.get('rowspan', '1') != '1' for cell in [*groups, *columns]
    ) or any(cell.get('colspan', '1') != '1' for cell in columns):
        raise ValueError('Unsupported reference header spans')
    names=[' '.join(x.text_content().split()) for x in columns]
    if names[:3]!=['Data dostawy','Typ instrumentu','Kurs [PLN/MWh]']:
        raise ValueError('Wrong market/unit/columns')
    rows=[]
    for row in tables[0].xpath('./tbody/tr'):
        elements = row.xpath('./td')
        cells=[' '.join(c.text_content().split()) for c in elements]
        if not cells or not cells[0].startswith(day+'_H'): continue
        if len(elements) != len(names) or any(
            cell.get('colspan', '1') != '1' or cell.get('rowspan', '1') != '1'
            for cell in elements
        ):
            raise ValueError('Hourly reference row does not match header')
        if cells[1]!='60': raise ValueError('Bad hourly instrument row')
        label=cells[0].split('_',1)[1]
        if label not in expected:
            raise ValueError('Unexpected hourly label in Fix1 reference')
        if not re.fullmatch(r'[-−]?\d[\d ]*(?:,\d+)?',cells[2]):
            raise ValueError('Invalid reference number')
        rows.append((label,Decimal(cells[2].replace(' ','').replace('−','-').replace(',','.'))))
    observed = [label for label, _ in rows]
    if not observed:
        raise ValueError(f'No hourly Fixing I rows in TGE response for {day}')
    if observed != expected:
        raise ValueError(
            f'Incomplete or reordered hourly labels in Fix1 reference for {day}: '
            f'expected {len(expected)}, found {len(observed)}'
        )
    return rows


def _write_new_evidence(path, payload):
    # Exclusive creation leaves an existing path untouched, including on retry.
    stream = path.open('xb')
    try:
        with stream:
            stream.write(payload)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def _fetch_session(url, day):
    request = Request(url, headers={'User-Agent': 'model-b-energy-shadow-validation/1.0'})
    for attempt in range(3):
        delay = 2 ** attempt
        try:
            with urlopen(request, timeout=35) as response:
                if response.geturl() != url:
                    raise ValueError(f'TGE response URL differs from requested session for {day}')
                if response.status != 200:
                    raise ValueError(f'HTTP {response.status}')
                return response.status, response.read(MAX_HTML_BYTES + 1)
        except HTTPError as exc:
            exc.close()
            if attempt == 2 or exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get('Retry-After') if exc.headers else None
            if retry_after:
                try:
                    wait = int(retry_after)
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                        wait = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        raise exc from None
                delay = max(delay, wait)
                if delay > 10:
                    raise
        except (TimeoutError, URLError) as exc:
            if attempt == 2 or (isinstance(exc, URLError) and not isinstance(exc.reason, TimeoutError)):
                raise
        sleep(delay)


def capture(day, directory, *, autumn_25h=False):
    warsaw = ZoneInfo('Europe/Warsaw')
    start = datetime.combine(day, datetime.min.time(), warsaw)
    start_utc = start.astimezone(timezone.utc)
    length = (start + timedelta(days=1)).astimezone(timezone.utc) - start_utc
    if autumn_25h and length != timedelta(hours=25):
        raise ValueError('Autumn label reference requires a 25-hour Warsaw day')
    if not autumn_25h and length != timedelta(hours=24):
        raise ValueError('Standard reference requires a 24-hour Warsaw day')
    session=day-timedelta(days=1)
    url='https://tge.pl/energia-elektryczna-rdn?dateShow='+session.strftime('%d-%m-%Y')
    status, raw = _fetch_session(url, day)
    completed=datetime.now(timezone.utc).isoformat()
    if len(raw) > MAX_HTML_BYTES:
        raise ValueError(f'TGE response exceeds 8 MiB for {day}')
    rows=extract_reference(raw,day.isoformat(),autumn_25h=autumn_25h)
    stem='tge_delivery_'+day.isoformat()
    raw_path=directory/(stem+'.html');ref_path=directory/(stem+'.csv')
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    if autumn_25h:
        writer.writerow(['delivery_date', 'tge_hour_label', 'price_pln_mwh'])
        for label, price in rows:
            writer.writerow([day.isoformat(), label, format(price, 'f')])
    else:
        writer.writerow(['delivery_date', 'hour', 'price_pln_mwh', 'period_start_utc'])
        for index, (label, price) in enumerate(rows):
            period = start_utc + timedelta(hours=index)
            writer.writerow([day.isoformat(), label, format(price, 'f'), period.isoformat()])
    reference=out.getvalue().encode()
    _write_new_evidence(raw_path, raw)
    try:
        _write_new_evidence(ref_path, reference)
    except OSError:
        raw_path.unlink(missing_ok=True)
        raise
    return dict(delivery_date=day.isoformat(),session_date=session.isoformat(),url=url,
        retrieved_at_utc=completed,http_status=status,raw_file=raw_path.name,reference_file=ref_path.name,
        raw_sha256=sha256(raw).hexdigest(),reference_sha256=sha256(reference).hexdigest(),
        reference_method='independent lxml XPath extraction from SAME official HTML, not independent official export',
        hours=len(rows),bytes=len(raw),
        **({'period_basis': 'TGE_LABEL_ONLY'} if autumn_25h else {}))


def write_manifest(directory, manifest):
    # Publish the manifest only once the complete JSON has been written.
    temporary = NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, delete=False
    )
    staging_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(manifest, temporary, indent=2)
        staging_path.replace(directory / "manifest.json")
    finally:
        staging_path.unlink(missing_ok=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--start',required=True);p.add_argument('--days',type=int,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--autumn-label-reference', action='store_true',
                   help='Capture one 25-hour day as TGE labels without UTC mapping')
    args=p.parse_args()
    if not 1<=args.days<=7:p.error('Capture at most seven days per explicit invocation')
    if args.autumn_label_reference and args.days != 1:
        p.error('--autumn-label-reference requires --days 1')
    dates=[date.fromisoformat(args.start)+timedelta(days=i) for i in range(args.days)]
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        p.error(f'Output path already exists: {args.output}; choose a new directory')
    entries=[];errors=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(capture,d,args.output,autumn_25h=args.autumn_label_reference):d for d in dates}
        for task in as_completed(pending):
            d=pending[task]
            try:
                entry=task.result();entries.append(entry);print(json.dumps({'day':d.isoformat(),'hours':entry['hours'],'status':'captured'}),flush=True)
            except Exception as exc:
                errors.append({'day':d.isoformat(),'error':str(exc)});print(json.dumps(errors[-1]),flush=True)
    manifest=dict(entries=sorted(entries,key=lambda e:e['delivery_date']),errors=errors,
                  note='Historical captures obtained in one run; NOT seven days of operational SHADOW observation.')
    write_manifest(args.output, manifest)
    if errors:raise SystemExit(1)
