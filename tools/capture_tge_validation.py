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
from urllib.request import Request, urlopen


def extract_reference(raw, day):
    from lxml import html
    tree=html.fromstring(raw)
    tables=tree.xpath('//table[@id="rdn"]')
    if len(tables)!=1: raise ValueError('Expected one official RDN table')
    headers=tables[0].xpath('./thead/tr')
    if len(headers)!=2: raise ValueError('Header layout changed')
    groups=headers[0].xpath('./th')
    if len(groups)<3 or ' '.join(groups[2].text_content().split())!='Fixing I':
        raise ValueError('Third column group is not Fixing I')
    names=[' '.join(x.text_content().split()) for x in headers[1].xpath('./th')]
    if names[:3]!=['Data dostawy','Typ instrumentu','Kurs [PLN/MWh]']:
        raise ValueError('Wrong market/unit/columns')
    rows=[]
    for row in tables[0].xpath('./tbody/tr'):
        cells=[' '.join(c.text_content().split()) for c in row.xpath('./td')]
        if not cells or not cells[0].startswith(day+'_H'): continue
        if len(cells)<3 or cells[1]!='60': raise ValueError('Bad hourly instrument row')
        label=cells[0].split('_',1)[1]
        if not re.fullmatch(r'H(?:0[1-9]|1[0-9]|2[0-4])',label):
            raise ValueError('Capture utility supports standard days only')
        if not re.fullmatch(r'[-−]?\d[\d ]*(?:,\d+)?',cells[2]):
            raise ValueError('Invalid reference number')
        rows.append((label,Decimal(cells[2].replace(' ','').replace('−','-').replace(',','.'))))
    if [h for h,_ in rows]!=[f'H{i:02d}' for i in range(1,25)]:
        raise ValueError('Not a complete standard day in requested session')
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
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if errors:raise SystemExit(1)
