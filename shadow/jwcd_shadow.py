"""Independent OFFLINE SHADOW models. No PostgreSQL access or writes; no production imports.
Usage: python shadow/jwcd_shadow.py /path/to/history_jwcd_fix1.csv
Requires: numpy, pandas. Historical JWCD publication timestamps are absent from CSV,
so this diagnostic cannot certify real-time as-of feature availability.
"""
import sys
import numpy as np
import pandas as pd

src = sys.argv[1]
d = pd.read_csv(src, parse_dates=["delivery_date"])
assert not d.duplicated(["delivery_date", "hour"]).any()
assert d[["jwcd_mw", "fix1_pln_mwh"]].notna().all().all()
assert d.hour.between(1, 24).all()
assert (d.groupby("delivery_date").hour.agg(["size", "nunique"]) == 24).all().all()
dates = pd.date_range(d.delivery_date.min(), d.delivery_date.max())
assert len(dates) == d.delivery_date.nunique()
X = d.pivot(index="delivery_date", columns="hour", values="jwcd_mw").reindex(index=dates, columns=range(1,25)).to_numpy(float)
Y = d.pivot(index="delivery_date", columns="hour", values="fix1_pln_mwh").reindex(index=dates, columns=range(1,25)).to_numpy(float)
assert np.isfinite(X).all() and np.isfinite(Y).all()
idx = np.flatnonzero((dates >= "2025-10-03") & (dates <= "2026-10-02"))
assert len(idx) == 365
pred = {k: np.empty((len(idx), 24)) for k in ["OLS30", "OLS60", "OLS90", "SIM90_K10", "CONSENSUS_MEDIAN"]}
for j, i in enumerate(idx):
    for w in (30, 60, 90):
        x, y = X[i-w:i], Y[i-w:i]  # strictly prior dates
        xm, ym = x.mean(axis=0), y.mean(axis=0)
        denom = np.sum((x-xm)**2, axis=0)
        b = np.divide(np.sum((x-xm)*(y-ym),axis=0), denom,
                      out=np.zeros(24), where=denom>1e-12)
        pred[f"OLS{w}"][j] = ym + b*(X[i]-xm)
    x, y = X[i-90:i], Y[i-90:i]
    levels = x.mean(axis=1); target = X[i]; tlevel = target.mean()
    shape = (x-levels[:,None])/np.maximum(x.std(axis=1), 1)[:,None]
    tshape = (target-tlevel)/max(target.std(), 1)
    dist2 = 0.5*((levels-tlevel)/max(levels.std(ddof=1),1))**2 + 0.5*np.mean((shape-tshape)**2, axis=1)
    nearest = np.argsort(dist2,kind="stable")[:10]
    weights = 1/(np.sqrt(dist2[nearest])+0.1)
    pred["SIM90_K10"][j] = np.average(y[nearest], axis=0, weights=weights)
    pred["CONSENSUS_MEDIAN"][j] = np.median(np.stack([pred[k][j] for k in
            ("OLS30","OLS60","OLS90","SIM90_K10")]), axis=0)
actual = Y[idx]
for k, p in pred.items():
    error = p-actual
    mae = np.abs(error).mean()
    wape = 100*np.abs(error).sum()/np.abs(actual).sum()
    bias = error.mean()
    print(f"{k:20s} MAE={mae:.4f} WAPE={wape:.4f}% bias={bias:.4f} n={error.size}")
    for name, s in (("H01-H06",slice(0,6)),("H11-H16",slice(10,16)),("H18-H22",slice(17,22))):
        print(f"  {name} MAE={np.abs(error[:,s]).mean():.4f}")
print("MAPE deliberately omitted: zero actual prices exist.")
