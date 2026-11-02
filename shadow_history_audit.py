"""Read-only CSV audit; no DB connections or production imports.

Supports legacy H01..H24 ONLY on standard days; DST needs explicit UTC periods.
A structurally complete price day alone never means model-ready history.
"""
import csv
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from shadow_scoring import WARSAW, _utc, expected_periods


def _number(value):
    try: return value not in (None,'') and Decimal(str(value)).is_finite()
    except InvalidOperation: return False


def _stamp(value):
    if isinstance(value,datetime): return _utc(value)
    return _utc(datetime.fromisoformat(str(value).replace('Z','+00:00')))


def audit_rows(rows):
    groups=defaultdict(list); invalid_dates=0
    for row in rows:
        try: d=date.fromisoformat(row['delivery_date'])
        except (ValueError,KeyError,TypeError): invalid_dates+=1; continue
        groups[d].append(row)
    days=[]
    for d,records in sorted(groups.items()):
        periods=expected_periods(d); expected=set(periods); seen=[]; issues=set()
        good_prices=good_jwcd=available=verified=0
        cutoff=_utc(datetime.combine(d-timedelta(days=1),time(9,50),WARSAW))
        for r in records:
            try:
                if r.get('period_start_utc'):
                    p=_stamp(r['period_start_utc'])
                elif len(periods)==24:
                    label=str(r.get('hour','')).removeprefix('H')
                    h=int(label)
                    if not 1<=h<=24: raise ValueError('hour')
                    p=periods[h-1]
                else:
                    raise ValueError('DST_REQUIRES_UTC_PERIODS')
                seen.append(p)
            except (ValueError,TypeError,IndexError): issues.add('INVALID_OR_AMBIGUOUS_PERIOD')
            if _number(r.get('fix1',r.get('price_pln_mwh'))): good_prices+=1
            if _number(r.get('jwcd_mw')): good_jwcd+=1
            if r.get('actual_verified') in (True,'true','True'): verified+=1
            try:
                pub=_stamp(r.get('publication_ts')); start=_stamp(r.get('retrieved_at_utc'))
                end=_stamp(r.get('retrieval_completed_at'))
                if start<=end<=cutoff and pub<=end: available+=1
            except (ValueError,TypeError): pass
        complete=len(records)==len(periods) and len(seen)==len(set(seen)) and set(seen)==expected
        if not complete: issues.add('INCOMPLETE_OR_DUPLICATE_PERIODS')
        if good_prices!=len(periods): issues.add('MISSING_OR_INVALID_FIX1')
        if good_jwcd!=len(periods): issues.add('MISSING_OR_INVALID_JWCD')
        if available!=len(periods): issues.add('AVAILABILITY_NOT_PROVEN')
        if verified!=len(periods): issues.add('ACTUAL_NOT_VERIFIED')
        days.append(dict(day=d.isoformat(),expected_periods=len(periods),rows=len(records),
            unique_periods=len(set(seen)),complete_periods=complete,
            valid_fix1_rows=good_prices,valid_jwcd_rows=good_jwcd,
            availability_proven_rows=available,verified_actual_rows=verified,
            model_ready=not issues,issues=sorted(issues)))
    return dict(days=days,invalid_date_rows=invalid_dates,
                model_ready_days=sum(d['model_ready'] for d in days))


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(line for line in f if not line.startswith('#')))


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('csv',nargs='+');args=p.parse_args()
    rows=[r for name in args.csv for r in read_csv(name)]
    print(json.dumps(audit_rows(rows),indent=2))
