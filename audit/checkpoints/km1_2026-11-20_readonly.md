# KM1 read-only checkpoint — 2026-11-20 17:56 Europe/Warsaw

Source: Railway production `model-b-energy`, service `tge-collector`, deployment `c1fc89b0-c8e2-4741-856f-f15fb0f83337` (commit `59f5cceed5dfa7df5da64ab01e0070ac49de2a09`). Observed via read-only service log query.

## Verified
- 2026-11-20 09:45 UTC: REJECTED for delivery 2026-11-21, reason `Invalid Fixing I price for H01: '-'` (pre-publication placeholder, fail-closed).
- 2026-11-20 10:01:21 UTC: event `tge_fix1`, delivery 2026-11-21, rows=24, status=COMPLETE.
- 28 COMPLETE events from 10:01 UTC through 16:46 UTC; exactly one unique 24-hour sample across these log events.
- Last observed 16:46:35 UTC: COMPLETE, 24 rows, `score=null`.
- Example sample: H01=783.16, H12=420.02, H24=552.65 PLN/MWh.
- Railway health: 6 active services, no reported failures or active issues; PostgreSQL online.

## Not verified
- No SQL result returned from PostgreSQL. Collector emits COMPLETE after a DB transaction, but this is not an independent database row-count check.
- No independent reconciliation with official published Fixing I; no audited PK5/JWCD history or 30/60/90D readiness.
- No MAE/MAPE/WAPE/bias or SHADOW walk-forward results.
- Read-only Railway Function `km1-readonly-audit` contains a SELECT-only audit with transaction READ ONLY, but has no execution result in logs.

## Repository
- PR #2 files: `tge_parser_safe.py`, `tests/test_tge_parser_safe.py`, two reference CSV fixtures.
- GitHub lookup of commit `7760cc5` returned 422 (not found).
- Branch search returned `test/tge-pr2-validation-20261102` and `test/tge-pr2-validation-20261119`, not 20261101.

No production changes, archive writes, PR merge or model updates performed in this audit.
