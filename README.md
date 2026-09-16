# Model B Energy

Collector for official PSE Market Data API.

## Test
Set `BUSINESS_DATE=2026-09-17` and run `python collector.py`.

The collector queries `pk5l-wp` and `unav-pk5l`, follows PSE pagination and logs HTTP status, record counts, publication timestamps and retrieval time.
