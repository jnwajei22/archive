# TradeLocker multi-account model

Agentic Forex Desk stores every TradeLocker connection and every account discovered through it. Credentials remain encrypted at rest. Broker account IDs are internal routing data; authenticated APIs and MCP account selection use durable public IDs and case-insensitive account aliases.

## Resolution rules

Read operations resolve one immutable account context from exactly one of:

1. an `account_alias` supplied by the user;
2. an execution profile public ID or name; or
3. the user's single default analysis account.

Resolution is tenant-scoped and fails closed when the connection or account is disabled, the broker no longer reports the account, or the selector belongs to another user. Cache identity includes the user, connection, stored account record, raw broker account pair, environment, and server.

The resolver has three explicit modes:

- `default_read` is available only to read-only operations with no selector.
- `explicit_account` accepts an owned alias or safe account reference and overrides the default.
- `execution_profile` follows the owned, enabled profile directly to its stored account and connection. It never reads dashboard default state.

Demo snapshots and previews require a profile reference. A preview persists its profile, account record, connection, alias, broker account pair, environment, server, base URL, and demo classification. Submission accepts only the preview and idempotency key, reloads the profile route, and rejects any routing mismatch before contacting an order endpoint.

## Discovery and migration

Discovery upserts the unique `(connection, broker account ID, accNum)` identity. It updates broker metadata while preserving the public account ID, alias, default selection, and profile bindings. Accounts omitted by a later discovery are marked unavailable rather than deleted.

Connections carry a discovery version and one-time refresh marker. Migrated connections with usable encrypted credentials attempt one refresh; successful discovery clears the marker, while rejected credentials produce a reauthentication-required state. Normal dashboard renders read stored state and do not repeatedly call TradeLocker discovery.

On first startup, the former one-connection schema is migrated in place. Its encrypted credential value is copied without decryption or reconnection, the selected account becomes the default analysis account, the `hourly_forex_v1` strategy template is seeded, and a read-only execution profile is created for the migrated selection.

## MCP selectors

The safe account-facing tools are:

- `list_my_tradelocker_connections`
- `list_my_tradelocker_accounts`
- `get_account_status(account_alias=None)`
- `get_open_positions(account_alias=None)`
- `get_pending_orders(account_alias=None)`
- `list_execution_profiles`

Aliases and safe account references are the only account selectors accepted by read tools. Profile creation and mutation are intentionally limited to the authenticated web API and dashboard. This refactor adds no live broker-write capability; the existing demo-only execution safety boundary is unchanged.

The demo execution tools require an execution-profile reference for status, snapshot, review, and no-trade recording. Submission derives the profile exclusively from the immutable preview. Demo autonomy can be armed only through the authenticated dashboard/API for a bounded period, and the durable scheduler dispatches only armed, verified demo profiles. Live and unknown accounts remain read-only.

## Demo Manual execution

Only an enabled, owned `demo_manual` profile bound to a verified demo account may create new entry previews or submissions. The public MCP workflow is:

- `get_demo_execution_status(profile_id)`
- `get_demo_trading_snapshot(profile_id, symbol)`
- `review_demo_order(profile_id, symbol, side, order_type, stop_loss, take_profit, reason, entry=None)`
- `submit_demo_order(preview_id, idempotency_key)`
- `get_demo_execution_result(execution_id)`
- `review_cancel_demo_order(profile_id, order_id)` / `submit_cancel_demo_order(preview_id, idempotency_key)`
- `review_close_demo_position(profile_id, position_id)` / `submit_close_demo_position(preview_id, idempotency_key)`

Entry submission never accepts quantity or broker routing fields. The backend derives account routing from the profile, resolves TradeLocker instrument metadata, calculates size using the stop distance, contract size, currency conversion, spread, commission, leverage, broker increments, and available margin, then rounds down. The defaults are 0.25% risk, 3% daily loss cutoff, 10% drawdown cutoff, one open position, one pending entry, two new entries per day, and 1.5 minimum reward-to-risk. Risk per trade has a hard 1% ceiling.

Snapshots and previews expire after 60 seconds. Entry previews include mandatory stop loss and take profit. The entry request sends both protections atomically and reconciliation verifies quantity, side, stop, and target. If a filled position is found without both protections, the service records `protection_failure`, attempts a full emergency close, and verifies whether the account is flat.

Cancellation uses TradeLocker's documented `DELETE /trade/orders/{orderId}` operation. Full-position close uses `DELETE /trade/positions/{positionId}` with `qty: 0`. Both require an immutable preview, use durable user-scoped idempotency, reload their profile route, and reconcile by confirming that the target disappeared. Ambiguous write responses are recorded as `unknown` and are never blindly retried.

The kill switch blocks new entry snapshots, previews, and submissions. Risk-reducing cancel and close actions remain independently validated and available. MCP can enable the switch but cannot disable it.
