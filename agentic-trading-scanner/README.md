# Agentic Forex Desk

Agentic Forex Desk is a focused forex research and execution-data backend for ChatGPT. It retrieves and normalizes financial data and now includes a presentation-only MCP Apps chart widget. ChatGPT still calculates overlays, technical analysis, scenarios, forecasts, and final trade planning.

## Provider authority

- **TradeLocker** is authoritative for the connected user's broker account, symbols, bid/ask quotes, spreads, forex candles used for execution analysis, balance, equity, margin, positions, pending orders, and deterministic risk-reviewed order previews.
- **Finnhub** supplies optional economic-calendar events, concise market news, cross-market context, and explicitly selected secondary forex data when the configured subscription permits it.
- **FRED/ALFRED** supplies official macroeconomic metadata, observations, release dates, real-time periods, and revision-aware historical context.
- **ChatGPT** calculates indicators, Fibonacci levels, support/resistance, strategies, and forecasts. The MCP Apps widget only renders the supplied values.
- **TradingView webhook** is optional untrusted inbound alert data. It is disabled without a secret, is not verified market data, and cannot place trades.

Finnhub and FRED values never replace TradeLocker prices for entry, stops, sizing, margin, previews, or execution. Source disagreements remain separate and explicitly labeled; the backend never averages or synthesizes cross-provider prices.

## Interactive snapshot charts

PNG generation remains removed: no image, public chart URL, or chart file is generated. Phase 1 uses Lightweight Charts only as a locally bundled renderer inside a sandboxed ChatGPT iframe. Each chart is an interactive snapshot with candlesticks, zoom, pan, crosshair, optional volume, supplied price/line overlays, and markers. It does not poll or continuously update after the tool call.

When a user asks for a chart:

1. ChatGPT calls `get_market_candles` with the requested range.
2. It verifies `complete`, `actual_start`, `actual_end`, and `candles_returned`, then calculates any requested Fibonacci or indicator values.
3. It calls `render_market_chart` with the returned user-scoped `series_id` and those overlays.
4. It must not claim a chart exists unless `render_market_chart` succeeds and the iframe is visible.

## Main MCP tools

- `get_market_candles`: canonical TradeLocker or explicitly selected Finnhub OHLCV.
- `render_market_chart`: displays a cached series in the versioned interactive MCP Apps resource; it retrieves no data and calculates no indicators.
- `get_account_status`: returns only the authenticated user's selected TradeLocker account as a stable, labeled schema. It maps positional state values using that account's `/trade/config`; zero balances remain valid.
- `get_paper_account_status`: returns the explicitly separate internal paper-account state and never substitutes for TradeLocker.
- `get_watchlist_market_data`: bounded close-only or selected-field TradeLocker series without ranking.
- `get_economic_calendar`, `get_market_news`: optional Finnhub data.
- `search_macro_series`, `get_macro_series`, `get_macro_release_calendar`: official FRED data.
- `get_forex_research_bundle`: bounded sections kept separate by source.
- TradeLocker connection, account, quote, symbols, positions, pending orders, preview, and kill-switch tools.
- Verified TradeLocker demo execution tools with immutable snapshots/previews, server-side sizing, durable idempotency, and reconciliation. See [docs/tradelocker-demo-execution.md](docs/tradelocker-demo-execution.md).

Example:

```json
{
  "name": "get_market_candles",
  "arguments": {
    "symbol": "EURUSD",
    "timeframe": "1H",
    "source": "tradelocker",
    "lookback": 300
  }
}
```

Canonical responses contain UTC ISO-8601 timestamps, oldest-to-newest candles, provider identity, requested and actual ranges, pagination metadata, completeness, warnings, and client-side rendering instructions. The default response is 300 candles and the maximum response is 2,000. The retrieval safety ceiling remains 10,000. Oversized requests return `response_too_large`; OHLC candles are never silently sampled or synthesized.

## Configuration

Copy `.env.example` and preserve the existing Auth0/OAuth, onboarding assertion, database, and `BROKER_SECRET_KEY` settings.

Finnhub keys are obtained from the Finnhub account dashboard. Enable it with `FINNHUB_ENABLED=true`. Economic calendar and forex endpoints may require paid capabilities; permission failures are cached and returned as `capability_unavailable`.

FRED keys are obtained from the St. Louis Fed API key page. Enable it with `FRED_ENABLED=true`. Configure currency mappings through `MACRO_CATALOG_JSON`; the repository intentionally ships no unverified default series IDs.

Both public providers use isolated in-process TTL caches. Successful market series are cached for ten minutes by authenticated user and cryptographic `series_id`, with a default global maximum of 100 entries. Credentials and authorization data are never cached. A persistent cache can replace the isolated interface later.

TradeLocker account-field configuration is cached for 15 minutes by Auth0 user, environment, server, account ID, and account number. The cache is invalidated when credentials are replaced, an account is selected, or the connection is removed. Account status fails closed instead of returning an unlabeled positional array when the mapping cannot be verified.

## Deployment

The provider-neutral domain, capability registry, generic trading compatibility APIs, canonical instrument mapping, runtime configuration lifecycle, and credential key rotation are documented in [Provider and configuration architecture](docs/provider-architecture.md). Users never configure environment variables. Large local `.env` files remain compatibility tooling; production uses typed defaults, database-backed runtime/user settings, and a small managed bootstrap-secret layer.

Build and test the locally bundled widget before starting or packaging the backend:

```text
npm --prefix widget ci
npm --prefix widget test
npm --prefix widget run build
```

Then install backend dependencies with `pip install -r requirements.txt` and restart the backend. Startup fails clearly if `widget/dist/index.html` is missing. On first startup, SQLite automatically adds the TradeLocker connection environment column and infers existing standard live URLs; no manual migration command is required. Disconnect and reconnect (or refresh) the ChatGPT app after deployment so it discovers the updated account-status schema and tools.

Redeploy the Vercel onboarding frontend because it now sends the selected TradeLocker environment explicitly. For Raspberry Pi deployments, keep system time synchronized, protect `.env`, use persistent PostgreSQL as already supported, and expect in-process caches to clear on restart.

Live trading remains disabled and the kill switch enabled:

```text
LIVE_TRADING_ENABLED=false
KILL_SWITCH_ENABLED=true
```

The backend implements only explicitly enabled, profile-bound TradeLocker **demo** execution, including the durable demo scheduler documented under `docs/`. It does not implement live execution, a Pine interpreter, strategy optimization, or a backtester.

ChatGPT-first autonomous control is exposed through `start_autonomous_trading`, `stop_autonomous_trading`, `emergency_stop_autonomous_trading`, and `get_autonomous_trading_status`. Scheduled cycles require a separate persistent worker; see [Persistent autonomous worker](docs/autonomous-worker-deployment.md). MCP requests never launch worker subprocesses.
