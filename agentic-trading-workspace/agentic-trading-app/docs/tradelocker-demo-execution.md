# TradeLocker demo execution

This workflow performs consequential broker-side writes only against the authenticated user's profile-bound, verified TradeLocker demo account. It does not implement live execution. Internal paper trading remains separate.

## Safety architecture

Execution settings are keyed by authenticated user, connection, account ID, and account number. New account records default to `read_only`; `demo_manual` and `demo_autonomous` can be selected only through the authenticated settings API/UI. MCP tools cannot change this setting. The existing MCP kill-switch operation remains one-way: it can enable the switch but cannot disable it.

Every snapshot, preview, and submission resolves the profile-bound connection internally. Demo execution requires all of the following to agree:

- the stored connection environment is `demo`;
- its normalized base URL exactly equals `TRADELOCKER_DEMO_BASE_URL`;
- the selected account is rediscovered using that connection and matches both account ID and `accNum`;
- the account-scoped execution mode permits demo execution;
- all records belong to the authenticated user and current selected account.

The submit tool accepts only `preview_id` and `idempotency_key`. It cannot accept an account, environment, URL, instrument, side, price, quantity, stop, or target. Market and limit orders include absolute stop-loss and take-profit fields in the initial TradeLocker order request.

OAuth additionally requires `forex:execute` for submission; read or preview permission alone cannot call the broker-write tool.

## MCP workflow

1. `get_autonomous_demo_status` checks readiness without mutation.
2. `get_autonomous_demo_snapshot` stores an immutable, short-lived normalized account/market/provider snapshot.
3. `review_autonomous_demo_order` accepts a setup but calculates final size on the server and stores an immutable, short-lived preview.
4. `submit_autonomous_demo_order` claims that preview durably, refreshes account/provider/quote/order state, then submits at most once.
5. `record_autonomous_no_trade` records a deliberate no-trade decision without calling a broker order endpoint.
6. `get_autonomous_run_result` reads the latest or requested owned result.

Allowed pairs are initially `EURUSD`, `GBPUSD`, `AUDUSD`, `NZDUSD`, and `USDCAD`. Stop loss and take profit are mandatory, reward/risk must be at least 1.5, risk is capped at 1%, and only one open position and one pending order are permitted. The 3% daily-loss and 10% drawdown cutoffs are rechecked immediately before submission. Finnhub calendar availability/freshness is required and fails closed. FRED also fails closed when the bound strategy template declares required macro series.

## Position sizing

The preview reads the TradeLocker instrument listing/details and requires verified instrument ID, TRADE route, contract size, pip size, minimum lot quantity, lot increment, quoting currency, and minimum stop distance. Risk per lot is:

`(absolute stop distance + current spread) × contract size × quote-to-account conversion + commission`

The risk budget uses the configured percentage of current balance. Lots are always rounded down to the broker increment and checked against broker minimum/maximum values. USD-quoted pairs use a 1:1 quote-to-account conversion for USD accounts. USD-base pairs such as USDCAD use the inverse current quote to convert CAD risk to USD. Other account currencies use a direct or inverse TradeLocker conversion quote from the bounded snapshot and are refreshed before submission; unavailable conversion pairs fail closed. The broker `qty` is the calculated lot quantity; the preview also reports underlying units as `quantity`.

## Durability and reconciliation

SQLite creates `execution_settings`, `autonomous_snapshots`, `autonomous_order_previews`, `autonomous_runs`, and `broker_submissions` automatically at startup. Unique constraints cover preview ID, idempotency key, and broker order ID. A `BEGIN IMMEDIATE` claim occurs before the broker call. Concurrent duplicates return the existing state. Any timeout during dispatch, or any failure after dispatch during reconciliation, is persisted as `unknown` and requires manual review; it is never blindly resubmitted.

Reconciliation maps pending orders, order history, and positions with their matching `/trade/config` columns. It verifies order ID/instrument, side, broker lot quantity, stop loss, and take profit. Positional arrays are never returned by the autonomous tools. Persisted broker responses contain only sanitized identifiers and acceptance state.

## Deployment

No standalone migration command is needed for the current SQLite repository; restart the backend once so the tables are created. Set these environment variable names as appropriate without exposing their values:

- `BROKER_SECRET_KEY`
- `SQLITE_PATH`
- `TRADELOCKER_DEMO_BASE_URL`
- `FINNHUB_ENABLED`
- `FINNHUB_API_KEY`
- `FRED_ENABLED`
- `FRED_API_KEY`
- `KILL_SWITCH_ENABLED`
- `AUTONOMOUS_SNAPSHOT_TTL_SECONDS`
- `AUTONOMOUS_PREVIEW_TTL_SECONDS`
- `AUTONOMOUS_QUOTE_MAX_AGE_SECONDS`
- `AUTONOMOUS_PRICE_TOLERANCE_PERCENT`
- `AUTONOMOUS_MAX_SPREAD_PIPS`
- `AUTONOMOUS_NEWS_BLACKOUT_MINUTES`

Keep `KILL_SWITCH_ENABLED=true` until an administrator intentionally enables demo testing in the deployment configuration. Never expose a remote endpoint that disables it.

## Manual demo verification

Do not perform this procedure on a live account.

1. Connect a TradeLocker demo account and select it.
2. Confirm normalized account status and `environment: demo`.
3. In Settings, explicitly set `demo_manual`.
4. Have the deployment administrator disable the kill switch and restart the backend.
5. Call status, then retrieve a fresh snapshot.
6. Review one allowed pair with a valid stop, target, and at least 1.5 reward/risk.
7. Inspect the server-calculated minimum practical demo quantity.
8. Submit once with a unique idempotency key.
9. Retrieve the run result and independently inspect the TradeLocker order/position.
10. Confirm side, lot quantity, stop loss, and take profit.
11. Retry the same preview/key and confirm no second broker order exists.
12. Enable the MCP kill switch and confirm another preview/submission is blocked.
13. Confirm no live account was modified.

## Current limitations

There are no trailing stops, partial closes, multiple targets, autonomous modifications, multiple simultaneous positions, or live execution. Currency combinations without an available direct or inverse TradeLocker conversion symbol fail closed. Manual broker verification remains required for an `unknown` submission. Scheduling is demo-only and subject to the same profile, arming, risk, idempotency, and reconciliation gates.
