# SHADOW checkpoint — 2026-11-10 18:00 Europe/Warsaw

Scope: read-only inspection of Railway and GitHub; no production changes.

## Evidence
- Railway project model-b-energy, production: Postgres SUCCESS; model-b-forecast SUCCESS, cron 06:40 UTC; tge-collector SUCCESS, cron */15; km1-readonly-audit SUCCESS deployment dated 2026-11-01, no cron.
- tge-collector runtime log 2026-11-10T17:00:46Z: delivery_date=2026-11-11, rows=24, status=COMPLETE. This is *not* evidence of correct prices.
- model-b-forecast log 2026-11-10T06:42:28Z: forecast_id=50, delivery_date=2026-11-11, snapshot_id=60, FROZEN, summary avg=674.33 min=513.8 max=892.14.
- PR #2 OPEN/DRAFT, head shadow/tge-parser-validation-20261029. The branch test/tge-pr2-validation-20261101 returned 404; commit 7760cc5 returned 422.
- Test branch test/tge-pr2-validation-20261102 has isolated Fixing I/II parser and unit tests. CI workflow added in commit 330fb3f; no workflow runs observed at checkpoint time.
- No direct read-only SQL execution available in the current Railway connector; therefore no fresh DB date ranges, pairing, duplicates, pre-fixing validation or 30/60/90-day readiness verified.
- Original TGE HTML and official 24/24 Fixing I and II comparison not yet verified.

## Safety gates before any production integration
1. Capture official raw HTML with timestamp and SHA256; identify header-to-column mapping for both Fixing I and Fixing II (do not assume positional column for Fixing II).
2. Confirm 24 distinct hourly contracts (type 60), exclude quarter-hour contracts; account for DST day 25.10.2026 separately.
3. Compare all 24 values against official published prices for multiple days.
4. Stage append-only observations with source/hash/timestamp; prohibit overwrite of existing actuals.
5. Keep forecast scoring disabled on unverified actuals.
6. Run independent walk-forward only after verifying aligned pre-fixing JWCD and actual Fixing I dates.

No production deployment, PR merge, FROZEN or archive edits in this checkpoint.
