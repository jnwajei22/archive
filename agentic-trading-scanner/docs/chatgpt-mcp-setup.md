# ChatGPT MCP OAuth Setup

Agentic Forex Desk exposes a Streamable HTTP MCP server at `https://mcp.justinnwajei.com/mcp/`. It retrieves normalized TradeLocker, Finnhub, and FRED data plus risk-checked order previews. Its presentation-only MCP Apps component renders supplied chart data, but the backend does not generate images or calculate indicators, rank trades, or submit live orders.

## Configure an OAuth provider

Use an external OIDC/OAuth provider such as Auth0. `AUTH_ISSUER` is required whenever `MCP_REQUIRE_OAUTH=true` and must be the real issuer URL published by that provider; it is not the MCP server URL and cannot be a placeholder. Do not place a client secret in this server: the MCP server is the protected resource and verifies access tokens issued to ChatGPT.

If OAuth is required and `AUTH_ISSUER` is empty, the protected-resource metadata endpoint returns a `503` configuration error. This prevents it from advertising an empty `authorization_servers` list, which ChatGPT cannot use to begin OAuth discovery.

For Auth0:

1. Create an API with the identifier (audience) `https://mcp.justinnwajei.com`, or use another audience and set `AUTH_AUDIENCE` to it.
2. Add API permissions named `forex:read`, `forex:preview`, and `forex:execute`.
3. Create or configure an application that ChatGPT can use with OAuth authorization code flow and PKCE. Add the callback URL shown by ChatGPT when creating the connector.
4. Ensure issued access tokens are JWTs, include the granted permissions in the `scope` claim, and use an asymmetric signing algorithm such as RS256.
5. Use the provider's exact issuer URL, including a trailing slash when the provider publishes one.

Configure the deployment environment:

```dotenv
MCP_REQUIRE_OAUTH=true
MCP_ALLOW_PUBLIC_NO_AUTH=false
AUTH_ISSUER=https://agentic-forex.us.auth0.com/
AUTH_AUDIENCE=https://mcp.justinnwajei.com
AUTH_JWKS_URL=https://agentic-forex.us.auth0.com/.well-known/jwks.json
OAUTH_AUTHORIZATION_URL=https://mcp.justinnwajei.com/oauth/authorize
OAUTH_TOKEN_URL=https://mcp.justinnwajei.com/oauth/token
OAUTH_TRANSACTION_SECRET=<long-random-server-side-secret>
OAUTH_ALLOWED_CLIENT_IDS=<comma-separated-ChatGPT-OAuth-client-IDs>
```

Replace `agentic-forex.us.auth0.com` with the domain assigned by Auth0, or use the issuer and JWKS URL published by your chosen OIDC/OAuth provider. Preserve the issuer's trailing slash when the provider publishes one; JWT issuer validation requires an exact match.

The audience defaults to `https://mcp.justinnwajei.com` when `AUTH_AUDIENCE` is empty. Restart the server after changing these values.

The public protected-resource metadata document is available at:

```text
https://mcp.justinnwajei.com/.well-known/oauth-protected-resource
```

It identifies the Agentic Forex Desk authorization server and the supported `forex:read`, `forex:preview`, and `forex:execute` scopes. The server stores ChatGPT's authorization request in SQLite while Auth0 establishes the portal identity and TradeLocker onboarding completes. Only the **Use with ChatGPT** action causes Agentic Forex Desk to issue a short-lived authorization code. ChatGPT exchanges that code at the Agentic Forex Desk token endpoint using the original PKCE verifier. An unauthenticated request to `/mcp/` receives a `401` response pointing ChatGPT to that metadata document through `WWW-Authenticate`.

The authorization-server metadata advertises Client ID Metadata Documents (CIMD) as the preferred client-identification mechanism. When ChatGPT supplies an HTTPS CIMD URL as `client_id`, the backend fetches it with a short timeout and response-size limit, rejects unsafe network destinations and redirects, validates the exact callback URI and public-client `none` method, and briefly caches the validated document. `OAUTH_ALLOWED_CLIENT_IDS` is only an optional compatibility allowlist for predefined static clients; arbitrary static IDs are rejected. No dynamic registration endpoint is advertised.

## Connect from ChatGPT

In ChatGPT, enable developer mode for connectors/apps if your plan and workspace permits it. Create a custom MCP connector using:

```text
https://mcp.justinnwajei.com/mcp/
```

Choose OAuth and enter the client/provider values requested by ChatGPT. Request `forex:read`, `forex:preview`, and `forex:execute` so all connector tools are available. The execute scope is required only for the consequential TradeLocker demo submission tool.

## Silent OAuth renewal

The authorization server advertises both `authorization_code` and `refresh_token` grants while retaining the existing PKCE S256 authorization-code flow. Every successful code exchange now returns an opaque refresh token alongside the one-hour bearer token. Refresh tokens are stored only as SHA-256 hashes, are bound to the Auth0 subject, OAuth client, granted scopes, and MCP resource, and rotate on every use.

A successful refresh returns a new access token and a new refresh token. The previous refresh token becomes unusable atomically. Reuse detection revokes the remaining token family, preventing concurrent replay from creating multiple usable refresh credentials. A refresh may retain or narrow scopes but cannot add permissions. The MCP middleware resolves refreshed access tokens through the same stored-token claims path, preserving the original Auth0 subject.

Existing deployments create the `oauth_refresh_tokens` table and indexes automatically on restart. Existing authorization-code and broker records are retained. Connections authorized before this deployment do not possess a refresh token, so reconnect ChatGPT once; subsequent access-token renewal can be silent until the rotating refresh credential expires or is revoked.

Configure `OAUTH_ACCESS_TOKEN_TTL_SECONDS` (default `3600`) and `OAUTH_REFRESH_TOKEN_TTL_SECONDS` (default `7776000`, 90 days). Do not place token values in logs or configuration files. Refresh-token support does not change TradeLocker login, token refresh, credential encryption, connection ownership, or selected-account state.

TradeLocker account discovery is a two-step flow. Configure `TRADELOCKER_BASE_URL`, `TRADELOCKER_USERNAME`, `TRADELOCKER_PASSWORD`, and `TRADELOCKER_SERVER`, then run `get_tradelocker_accounts`. Copy the returned `accountId` and `accNum` into `TRADELOCKER_ACCOUNT_ID` and `TRADELOCKER_ACCOUNT_NUMBER` before using account-specific config, status, positions, symbols, quotes, or candles. Discovery never returns the configured password or TradeLocker tokens.

- `get_market_candles`, `get_watchlist_market_data`, provider research tools, account status, positions, pending orders, and quotes with `forex:read`
- `review_forex_order` with `forex:preview`
- `set_kill_switch` with `forex:preview`; remote callers cannot disable it

TradeLocker execution submission tools are intentionally not registered with the MCP server.

TradeLocker is the default and authoritative source for `get_market_candles`. Finnhub forex candles must be selected explicitly and remain secondary context. No provider failure triggers an implicit fallback.

`get_account_status` is always the current authenticated user's selected TradeLocker account. It loads the matching account configuration, labels the positional account-state values, preserves legitimate zero balances, and fails closed if the field mapping cannot be verified. Internal paper state is available only through the separately named `get_paper_account_status` tool.

For a visible chart, ChatGPT must call `get_market_candles`, verify completeness metadata, calculate requested overlays, and call `render_market_chart` with the returned `series_id`. `get_market_candles` alone does not display a chart. The result opens a locally bundled interactive snapshot in a ChatGPT iframe through `ui/notifications/tool-result`; it does not poll after the call. The backend has no chart route, image generation, or chart filesystem storage.

Build the widget before deploying the backend:

```powershell
npm --prefix widget ci
npm --prefix widget test
npm --prefix widget run build
```

Include `widget/dist/index.html` in the backend deployment artifact. After deployment, refresh or reconnect the ChatGPT app so it reloads the tool schema and versioned UI resource. Automatic refresh and multi-pane indicators are later phases.

The backend performs a small automatic SQLite migration that adds the stored TradeLocker environment for existing connections. Standard `live.tradelocker.com` connections are backfilled as live; other existing rows retain demo and should be reconnected if they use a custom live base URL. Redeploy the Vercel frontend so future onboarding requests send `environment` explicitly.

## Vercel frontend and multi-user broker storage

For a Vercel frontend calling the Raspberry Pi API through Cloudflare Tunnel, configure:

```dotenv
FRONTEND_ORIGIN=https://app.agenticforexdesk.com
SQLITE_PATH=storage/app.db
BROKER_SECRET_KEY=<long-random-secret-kept-only-on-the-backend>
ALLOW_ENV_BROKER_FALLBACK=false
```

The `/api/*` onboarding routes require an Auth0 access token. Users are keyed by the token's immutable `sub` claim, and each TradeLocker password is encrypted before it is written to SQLite. Keep `BROKER_SECRET_KEY` stable and backed up securely; changing or losing it makes existing encrypted connections unreadable. Never expose this key to Vercel or browser code.

The browser onboarding sequence is: save TradeLocker credentials, discover accounts, select an `accountId` and `accNum`, then query broker status. MCP tools resolve the same saved connection from the authenticated caller's Auth0 `sub`. Environment-based TradeLocker credentials are disabled unless `ALLOW_ENV_BROKER_FALLBACK=true` is deliberately enabled for local/manual testing.

## Run locally

Install dependencies and start the combined FastAPI/MCP process:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

OAuth remains required on localhost by default. For a local or manual client that cannot perform OAuth, the existing shared-secret mode is available only when explicitly selected:

```dotenv
MCP_REQUIRE_OAUTH=false
MCP_ALLOW_PUBLIC_NO_AUTH=false
MCP_SHARED_SECRET=<strong-random-test-secret>
```

Send that secret as `Authorization: Bearer <strong-random-test-secret>`. Never use shared-secret mode for the ChatGPT connector. Public no-auth mode remains an explicit test-only escape hatch and is disabled by default; OAuth takes precedence while `MCP_REQUIRE_OAUTH=true`.

## Safety reminders

- Live trading remains disabled regardless of MCP requests.
- Read-only MCP tools call TradeLocker for the authenticated user's account; no MCP tool submits an order.
- Do not put broker credentials, OAuth tokens, client secrets, webhook secrets, or shared secrets in ChatGPT instructions.
- Keep `MCP_ALLOW_PUBLIC_NO_AUTH=false` for every public deployment.
