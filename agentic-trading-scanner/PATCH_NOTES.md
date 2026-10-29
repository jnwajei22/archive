# Whole-market context patch

This patch fixes the mismatch where the autonomous snapshot contained broker-resolved whole-market candidates while the OpenAI decision context still advertised the legacy five-pair forex allowlist.

Changes:
- The decision context now treats the symbols actually present in the broker-resolved snapshot as authoritative.
- The snapshot now records market-universe provenance and screening metadata.
- Decision-context candle summaries now follow the timeframes actually present in each snapshot, including 5m.
- Added a regression test proving that stale legacy profile allowlists cannot override the broker-resolved runtime universe.

Verification performed in the sandbox:
- Targeted unit test module: 6 passed.
- Python compilation: passed.
- Full suite could not be collected in the sandbox because `fastmcp` is not installed there; this is an environment dependency issue, not a test failure in the patch.

After copying the patched files, rerun:

```powershell
python -m scripts.local_autonomous_verify preview
```

The decision output should now list the same allowed symbols that appear in the market snapshot instead of the inherited EURUSD/GBPUSD/AUDUSD/NZDUSD/USDCAD list.

## 15-minute schedule refactor

- Added environment-owned recurring execution cadence settings with a 15-minute default.
- Local startup now reconciles existing schedules, migrating the previous hourly schedule to 96 candle-aligned checks per configured day.
- New ChatGPT-created strategy schedules use 15-minute recurring intervals during the configured London/New York session window.
- Status/start responses expose `interval_minutes` for operational verification.

## TradeLocker market-data reliability refactor

This patch addresses autonomous runs that reached the scheduled cycle but failed every candidate with `*_quote_unavailable`.

Changes:
- Cache the full TradeLocker instrument catalog once per client instead of re-fetching it for every symbol resolution.
- Reuse cached INFO routes for quotes and candles.
- Use the documented TRADE route for instrument-detail requests.
- Pace quote and instrument-detail requests through the existing cross-process TradeLocker limiter and retry bounded 429 responses.
- Preserve sanitized per-symbol failure diagnostics (`code`, `status_code`, stage, retry delay) in the autonomous run validation payload.
- Rotate whole-market candidate windows between runs instead of repeatedly scanning the same alphabetic prefix.
- Stop reporting rejected or blocked previews as `Demo Order Submitted` activity events.

Verification performed in the sandbox:
- TradeLocker client, whole-market policy, autonomous execution, scheduler, controls, decision runner, and operational safety suites: 143 passed.
- Full unit-suite collection remains unavailable in the sandbox because `fastmcp` is not installed.
