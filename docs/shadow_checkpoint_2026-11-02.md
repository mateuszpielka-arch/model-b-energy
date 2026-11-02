# SHADOW checkpoint — 2026-11-02 (Europe/Warsaw)

## Scope and safety
- Branch: `test/tge-pr2-validation-20261102`, branched from draft PR #2 head (`shadow/tge-parser-validation-20261029`).
- The earlier local commit `7760cc5` was NOT present in the remote repository (GitHub returned 422), and branch `test/tge-pr2-validation-20261101` was NOT found.
- No changes to `main`, production Railway deployments, JWCD 90D, FROZEN or PostgreSQL archives.
- This branch is an **offline validation checkpoint**, not a production-ready parser or a deployed staging collector.

## Evidence
- `tge_fix1_staging_guard.py`: fail-closed check of the expected first three table headers (`Data dostawy`, `Typ instrumentu`, `Kurs [PLN/MWh]`), the `Fixing I` label, 24 unique hours, suspicious `60`/`2026` values, optional independent reference, and 23h/25h DST exclusion.
- `tests/test_tge_fix1_staging_guard.py`: **9/9 offline tests passed** using the existing PR #2 parser and official-reference prices for delivery 2026-11-02. Test HTML is **constructed**; it is NOT a raw TGE HTML capture.
- `tests/fixtures/tge_fix1_2026-11-03_reference.csv`: 24 Fixing I prices independently transcribed from the public TGE table on 2026-11-02; source: https://tge.pl/energia-elektryczna-rdn . Published TGe24 index = 1250.96 PLN/MWh; the mean of the 24 hourly Fixing I prices is also 1250.96.
- `tests/fixtures/tge_fix1_2026-11-03_frozen42_scoring.csv`: read-only comparison of production FROZEN #42 against the official TGE hourly reference. Global MAE 321.50 PLN/MWh, MAPE 25.48%, WAPE 25.70%, bias -316.97 PLN/MWh. H11-H16 MAE 465.37, MAPE 35.22%, WAPE 35.18%, bias -465.37. H18-H22 MAE 197.93, MAPE 13.91%, WAPE 14.02%, bias -176.21.
- The latest **completed** read-only PostgreSQL audit visible when this checkpoint was prepared was 2026-11-01 17:04:58 UTC: 51 JWCD snapshots, 1080 Fix1 rows, 0 validated price days, 30D/60D/90D NOT ready. Do not extrapolate this as a fresh 2026-11-02 SQL audit.

## Blockers and next gate
1. Fetch **actual raw HTML** from TGE in a network-capable, read-only test environment. Direct DNS access from the local isolated test container failed. Do not assert that the parser passed raw-HTML validation.
2. Confirm exact real table headers, `thead`/`tbody` structure, hourly instrument 60, price column index and date-specific content; compare **all 24** extracted prices against independent reference for multiple days.
3. Before any production integration, replace unsafe `ON CONFLICT DO UPDATE` archival writes with append-only or quarantine/verified-promotion flow; prevent scoring on unverified prices.
4. Handle 25-period DST delivery days with period identity, not H01-H24 labels alone.
5. Run SQL read-only audit on latest data; recover validated historical PK5/Fix1 pairs and pre-fixing timestamps before SHADOW walk-forward.

No production deployment, PR merge or PR closure is authorized by this checkpoint.
