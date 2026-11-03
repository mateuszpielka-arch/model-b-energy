# SHADOW checkpoint — 2026-11-03 (Europe/Warsaw)

## Scope, provenance and safety
- Existing repository: `mateuszpielka-arch/model-b-energy`; branch `test/tge-pr2-validation-20261102` only. Draft PR #2 remains open and unmerged.
- No deployment, merge, PR closure, PostgreSQL write, FROZEN modification, or production JWCD 90D change.
- Earlier local commit `7760cc5` is not available on remote GitHub (fetch returned 422). The older `test/tge-pr2-validation-20261101` branch is not present in branch search.
- Official source consulted on 2026-11-03: https://tge.pl/energia-elektryczna-rdn, delivery 2026-11-04. Publicly rendered table has 17 columns; first three are `Data dostawy`, `Typ instrumentu`, `Kurs [PLN/MWh]`, under `Fixing I`. Hourly keys `2026-11-04_H01` to `H24` have instrument type `60`. Fifteen-minute `Q` rows are interleaved.
- **No raw HTML capture** was obtained. The isolated container's direct HTTPS request failed at DNS resolution. The official site was read through the web text renderer; do not claim raw-HTML validation.

## New durable evidence
- `tests/fixtures/tge_fix1_2026-11-04_reference.csv`: 24 prices from the official TGE Fixing I column. Mean **1355.4333 PLN/MWh**, matching displayed TGe24 **1355.43** (cross-check only, not a substitute for 24 individual values).
- `tests/fixtures/tge_fix1_2026-11-04_frozen43_scoring.csv`: independent read-only comparison of Railway production FROZEN forecast #43 (snapshot #53, `jwcd-linear-hourly-90d-v1-20261006`) with the 24 official TGE prices.
- Metrics computed from the 24 hourly pairs (error = forecast minus actual):
  - Global (n=24): MAE **409.4483 PLN/MWh**, MAPE **29.3635%**, WAPE **30.2079%**, bias **-409.4483 PLN/MWh**.
  - H11–H16 (n=6): MAE **459.0450**, MAPE **33.4292%**, WAPE **35.1394%**, bias **-459.0450**.
  - H18–H22 (n=5): MAE **484.1340**, MAPE **26.8317%**, WAPE **26.9194%**, bias **-484.1340**.
  - Forecast mean **945.9850**, actual mean **1355.4333** PLN/MWh. All 24 hours were underestimated.
- `tests/test_tge_fix1_20261104.py`: **6/6 new offline tests passed** locally using copies of the existing PR #2 parser and staging/scoring modules. Tests cover all 24 official reference prices, constructed 17-column HTML with interleaved quarter-hour rows, shifted price header rejection, instrument-type-as-price rejection, missing/duplicate hourly rows, and verified FROZEN #43 scoring. Constructed HTML is **not** captured TGE HTML. The full prior 18-test suite was not rerun in this checkpoint.

## Railway / KM1 status
- Live `tge-collector` still runs deployment from 2026-10-06, commit `657fcc4dc7aa282675164236890f41bf2a7e3a07`. 2026-11-03 17:47 Warsaw runtime log: `COMPLETE` with 24 rows for 2026-11-04; this is **not proof of valid prices**.
- Latest read-only PostgreSQL audit actually present in `km1-readonly-audit` logs was run 2026-11-01 18:04 Warsaw: 51 JWCD snapshots (2026-09-18 through 2026-11-02), 1080 actual Fix1 rows (2026-09-19 through 2026-11-02), 45 days with actual rows, **0 price-quality-validated days**, 30D/60D/90D windows not ready. The DST delivery date 2026-10-25 had 25 JWCD periods versus 24 Fix1 rows. Tables `history.hourly` and `model.forecast_scores` absent at that audit.
- **No new SQL audit was run on 2026-11-03.** This connector provides logs and service management but no direct read-only SQL query execution. Railway Automations was not enabled for this account, and restarting/redeploying a production service was avoided under the no-production-deployment constraint. Do not present 2026-11-01 numbers as a fresh 2026-11-03 DB snapshot.

## Remaining gates
1. Capture actual raw TGE HTML in a network-capable, read-only environment and verify the precise table scope, colspan/thead/tbody structure, the first-three-column mapping, and all 24 reference prices for multiple days.
2. Implement an append-only/quarantine/verified-promotion archival flow; remove unsafe overwrite behavior before any production collector change. Preserve existing archives.
3. Provide an authorized read-only SQL execution route and rerun the latest PK5/Fix1 audit; check completeness, duplicates, pre-fixing timestamps, DST period identity, 30D/60D/90D pairing.
4. Recover and validate historical Excel + JWCD before any SHADOW walk-forward. No leakage; no Ridge, PV, wind, or P1/P2.
5. Keep 09:50 morning forecast and 18:00 evening report separate.

No production rollout is authorized by this checkpoint.

## Additional archive safety work (same checkpoint)
- Inspected the current repository `tge_collector.py`: its legacy collector contains `ON CONFLICT(delivery_date,hour) DO UPDATE`, which can overwrite existing hourly prices with a misparsed value; its `score()` reads legacy actuals without an independent reference check. This is a concrete code-level risk; it was **not** changed in production.
- Added `tge_shadow_archive_policy.py`: pure fail-closed proposal for promotion into a **separate verified archive**, never modifying legacy `actual.tge_fix1`. Conflicting verified hours or reference mismatches quarantine the **whole day**; matching records are idempotent; missing verified hours are proposed as append-only inserts.
- Added `tests/test_tge_shadow_archive_policy.py`: **6/6** offline tests passed (new day, idempotence, immutable conflict, reference mismatch, partial day, incomplete day).
- Together with the six 2026-11-04 parser/scoring tests, **12/12 newly introduced tests passed locally**. These tests do not prove production DB integration, raw-HTML compatibility, or successful execution of the full pre-existing suite.

## Root-cause reproduction on exact deployed collector code
- Fetched `tge_collector.py` at the **deployed** commit `657fcc4dc7aa282675164236890f41bf2a7e3a07`, not merely the current `main` version.
- Deployed parser strips all HTML tags to whitespace, then applies `re.findall(r'[-−]?\\d+(?:[ .]\\d{3})*(?:,\\d+)?', after)` and accepts the first number in [-5000, 20000]. On constructed 17-column TGE-like HTML, `60 983,00` is matched as a single oversized numeric token and rejected; the next token is year `2026`. For rows with four-digit prices, `60` is taken as the price. This reproduces **24/24 structurally complete but invalid rows**, whose distinct values are exactly `60` and `2026`.
- `tests/test_tge_legacy_failure_20261104.py` compares this exact deployed legacy algorithm against the strict PR #2 parser on the same constructed 24-hour fixture: **1/1 test passed**. Strict parser returns all 24 independently verified reference prices.
- **Important distinction:** deployed commit `657fcc4` uses `ON CONFLICT DO NOTHING`, so it does **not** overwrite existing prices, but permanently preserves the first wrong insert. Current repository `main` has `ON CONFLICT DO UPDATE` and a scoring path that may overwrite or score unverified prices. Neither version is safe for verified archiving.
- Total new tests run locally in this checkpoint: **13/13 passed** (6 parser/scoring, 6 archive policy, 1 legacy failure reproduction). Raw TGE HTML remains unavailable and was **not** tested.
