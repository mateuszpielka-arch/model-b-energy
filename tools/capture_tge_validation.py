"""Explicit read-only TGE validation capture; never archives production prices.

Optional lxml dependency provides extraction independent of tge_parser_safe.
No model/parser output is used to create the reference values.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen


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
    if [h for h,_ in rows] != expected:
        raise ValueError('Incomplete or reordered hourly labels in Fix1 reference')
    return rows


def capture(day, directory):
    session=day-timedelta(days=1)
    url='https://tge.pl/energia-elektryczna-rdn?dateShow='+session.strftime('%d-%m-%Y')
    with urlopen(Request(url,headers={'User-Agent':'model-b-energy-shadow-validation/1.0'}),timeout=35) as r:
        raw=r.read();status=r.status
    completed=datetime.now(timezone.utc).isoformat()
    if status!=200: raise ValueError(f'HTTP {status}')
    rows=extract_reference(raw,day.isoformat())
    stem='tge_delivery_'+day.isoformat()
    raw_path=directory/(stem+'.html');ref_path=directory/(stem+'.csv')
    out=io.StringIO();w=csv.writer(out,lineterminator='\n');w.writerow(['delivery_date','hour','price_pln_mwh'])
    for label,price in rows:w.writerow([day.isoformat(),label,format(price,'f')])
    reference=out.getvalue().encode()
    raw_path.write_bytes(raw);ref_path.write_bytes(reference)
    return dict(delivery_date=day.isoformat(),session_date=session.isoformat(),url=url,
        retrieved_at_utc=completed,http_status=status,raw_file=raw_path.name,reference_file=ref_path.name,
        raw_sha256=sha256(raw).hexdigest(),reference_sha256=sha256(reference).hexdigest(),
        reference_method='independent lxml XPath extraction from SAME official HTML, not independent official export',
        hours=len(rows),bytes=len(raw))


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
    args=p.parse_args()
    if not 1<=args.days<=7:p.error('Capture at most seven days per explicit invocation')
    dates=[date.fromisoformat(args.start)+timedelta(days=i) for i in range(args.days)]
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        p.error(f'Output path already exists: {args.output}; choose a new directory')
    entries=[];errors=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(capture,d,args.output):d for d in dates}
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
