# Provider and configuration architecture

The customer domain separates brokers, trading platforms, execution providers, market-data providers, chart providers, signal providers, connections, and accounts. A broker is the regulated or commercial account relationship; a platform or provider is the technical integration used for one or more roles.

## Registry and capabilities

`app.providers.registry` is the authoritative catalog. Capabilities are normalized and fail closed: omitted or unknown booleans are `false`. TradeLocker is registered for execution, account data, and broker market data. TradingView Chart is chart-only and is never a broker or execution provider. TradingView Signal is an authenticated intent-ingress role. Robinhood Agentic is an official MCP/OAuth boundary with equities/options types, but remains `not_configured`; no unofficial API, scraping, or browser automation exists. Alpaca and Interactive Brokers identifiers are reserved and disabled.

Legacy `/api/broker/*` routes remain compatibility aliases. New clients use `/api/providers` and `/api/trading/*`. Legacy TradeLocker rows are projected into generic connection/account responses without deleting legacy identifiers or columns. TradeLocker-specific account aliases remain internal compatibility references.

## Canonical instruments

Strategies, watchlists, URLs, and market APIs use canonical IDs such as `forex:EUR/USD`. The mapping service produces independent provider symbols—`OANDA:EURUSD` for TradingView and `OANDA:EUR_USD` for Finnhub. A provider symbol is never reused as another provider's symbol. Missing mappings return an explicit unsupported state.

TradingView charts are non-authoritative visual context. Signal ingress validates authentication, payload size/schema/time, replay/idempotency, and rate limits, then durably stores a pending signal intent. It never submits an order. Later processing must retrieve an authoritative execution-provider quote, apply strategy/risk and account-capability checks, preview, authorize, submit, and reconcile through the selected execution provider.

## Configuration lifecycle

Users never configure environment variables or provider API keys. Per-user connections, accounts, primary selection, nicknames, strategies, schedules, watchlists, regional settings, notifications, and personal safety controls are database-backed and Auth0-subject scoped.

Typed code defaults cover ordinary timeouts, retries, TTLs, page limits, polling, response limits, quote age, and confidence. Operator-changeable runtime settings live in `platform_runtime_settings` behind `AdminConfigurationService`, allowing safe changes without source edits. The customer-facing admin UI is intentionally not part of this change.

Production still requires a small managed secret layer for database/Redis credentials, Auth0, encryption keys, OAuth transaction/signing secrets, OpenAI, Finnhub, FRED, and webhook signing secrets. `EnvironmentSecretProvider` is local-development and compatibility tooling. `ManagedSecretProvider` is the production-host boundary. Large local `.env` files are not the production control plane.

## Credential encryption rotation

Credential rows carry an encryption key version. New records use the active version, retained older versions remain decryptable, and `reencrypt_credentials` performs gradual migration with audit rows. Missing or invalid old keys fail safely without returning ciphertext or key material. This implementation does not rotate any real key.

## Migration risks

The generic layer is additive. Legacy columns and routes cannot be removed until deployed clients, workers, and stored profiles no longer reference them. Profile V2 defaults to the existing forex projection; equities and options use separate validated extension blocks and must not be routed to a provider lacking reported capabilities.
