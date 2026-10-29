# Local autonomous TradeLocker service

This repository can run as a backend-only, single-owner TradeLocker node. `APP_MODE=local_autonomous` keeps the production OAuth code present but resolves all application and MCP operations to `LOCAL_USER_ID`. TradeLocker login credentials identify the broker login session; the separately configured account ID and account number identify the only account the node may use.

## Architecture

The entry pipeline is preserved and now always follows the management phase:

`embedded scheduler -> refresh positions/pending orders/protection/quotes -> reconcile unresolved management writes -> strict management decision -> deterministic close/reduce/protection validation -> idempotent management write and broker-state reconciliation -> only then scan for new entries`

The local bootstrap authenticates with TradeLocker and discovers all accounts. `TRADELOCKER_ACCOUNT_ID` is the authoritative selector. Exactly one discovered row must match it; that row's `accNum` is then used for every `/trade/*` header. `TRADELOCKER_ACCOUNT_NUMBER` is optional and, when present, is only a consistency assertion. Server comparison is case-insensitive and whitespace-normalized. The bootstrap never selects the first account and fails safely if the ID is absent, duplicated, or conflicts with the optional number, server, or environment.

TradeLocker reauthenticates on an HTTP 401 and caches tokens only in process memory. Credentials are encrypted in SQLite with `BROKER_SECRET_KEY`. OpenAI responses are parsed through a strict Pydantic schema; model output cannot choose an account, quantity, route, risk budget, or execution authority.

## Install and configure

Use Python 3.11 or newer:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.local-autonomous.example .env
```

Edit `.env` locally. Never commit it. The authoritative variable contract, including defaults and safe placeholders, is [.env.local-autonomous.example](.env.local-autonomous.example).

Required local variables are:

- `APP_MODE`, `LOCAL_USER_ID`, `DATABASE_URL`, and `BROKER_SECRET_KEY`
- `PUBLIC_BASE_URL`, `LOCAL_MCP_API_TOKEN`, and `ALLOW_UNAUTHENTICATED_PUBLIC_MCP`
- `TRADELOCKER_ENVIRONMENT`, `TRADELOCKER_BASE_URL`, `TRADELOCKER_EMAIL`, `TRADELOCKER_PASSWORD`, `TRADELOCKER_SERVER`, and authoritative `TRADELOCKER_ACCOUNT_ID`; `TRADELOCKER_ACCOUNT_NUMBER` is optional
- `OPENAI_API_KEY`, `AUTONOMOUS_DECISION_PROVIDER=openai`, and an API-accessible `AUTONOMOUS_DECISION_MODEL`
- the strategy, allow/block lists, timeframes, hard risk limits, scheduler settings, and three execution gates shown in the example

`TRADELOCKER_USERNAME` remains a compatibility alias. Prefer `TRADELOCKER_EMAIL`; it is a login credential, never an account selector. `SQLITE_PATH` remains a compatibility setting, while `DATABASE_URL=sqlite:///...` is authoritative when supplied. `MCP_SHARED_SECRET` and `MCP_ALLOW_PUBLIC_NO_AUTH` remain production/manual compatibility settings; local mode uses `LOCAL_MCP_API_TOKEN` and `ALLOW_UNAUTHENTICATED_PUBLIC_MCP`.

`AUTONOMOUS_STRATEGY_JSON` must be a JSON object. Startup rejects malformed JSON. Scalar environment risk settings are written into the deterministic profile and remain authoritative over the free-form prompt. Comma-separated values are trimmed, deduplicated, and normalized.

Whole-market history is cache-first and staged. `AUTONOMOUS_HTF_CANDIDATE_LIMIT`
bounds the metadata-screened universe, `AUTONOMOUS_LTF_CANDIDATE_LIMIT` bounds
1h/15m work, and `AUTONOMOUS_ENTRY_CANDIDATE_LIMIT` bounds final 5m work.
`AUTONOMOUS_MAX_HISTORY_REQUESTS_PER_CYCLE` is a hard ceiling on uncached broker
history calls. Rate-limited and budget-deferred work is saved in SQLite and resumed
by later scheduler cycles; incomplete symbols are never exposed as entry candidates.

## Startup and verification

Start safely with execution disabled and the kill switch enabled:

```powershell
python scripts/local_autonomous_verify.py config
python scripts/local_autonomous_verify.py accounts
# Deliberately reveal full IDs only when troubleshooting locally:
python scripts/local_autonomous_verify.py accounts --show-identifiers
python scripts/local_autonomous_verify.py tradelocker
python scripts/local_autonomous_verify.py openai
python scripts/local_autonomous_verify.py position-management
python scripts/local_autonomous_verify.py candles
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`accounts` is discovery-only and masks account IDs and `accNum` unless `--show-identifiers` is explicitly supplied. `tradelocker` authenticates, verifies the exact account ID, reports the resolved masked `accNum`, checks instrument access, and seeds local records. `position-management` authenticates, reads current positions/orders, checks adapter methods, storage and unresolved submissions, and never invokes a write endpoint. The OpenAI command checks model accessibility and typed-provider initialization. None of these commands submits an order.

After the server starts:

```powershell
$headers = @{ Authorization = "Bearer $env:LOCAL_MCP_API_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-RestMethod http://127.0.0.1:8000/scheduler/health
python scripts/local_autonomous_verify.py scheduler
python scripts/local_autonomous_verify.py preview
```

`preview` runs the complete decision path with `dry_run=True`; it cannot call order submission. A no-trade result is a valid dry-run outcome. Do not claim the node is execution-ready until `/ready` is healthy and this command completes against the intended account.

## MCP and Cloudflare tunnel assumptions

Point the tunnel at the loopback service and expose HTTPS only. Local MCP requests, and local administrative API routes, require `Authorization: Bearer <LOCAL_MCP_API_TOKEN>`. Comparison is constant-time. The token is never returned by health or readiness responses.

If `PUBLIC_BASE_URL` is public and the token is blank, settings validation fails unless `ALLOW_UNAUTHENTICATED_PUBLIC_MCP=true`. That exception is intended only for a deliberately isolated network. OAuth is bypassed only in local mode; production behavior is unchanged.

## Execution gates and kill switch

New-entry submissions recheck these values immediately before broker submission:

```dotenv
LIVE_TRADING_ENABLED=true
AUTONOMOUS_EXECUTION_ENABLED=true
KILL_SWITCH_ENABLED=false
```

The durable per-owner kill switch must also be off. A preview cannot bypass these checks. Timed process arming is retired: autonomous authority does not expire merely because the API or worker has remained healthy for 24 hours. `AUTONOMOUS_MAX_ARMING_HOURS` is accepted only for backward-compatible configuration parsing and is ignored.

Management has deliberately different gates. Every management write still requires `LIVE_TRADING_ENABLED=true`. Ordinary close, partial close, stop/TP, break-even, trailing, and cancellation actions require `AUTONOMOUS_POSITION_MANAGEMENT_ENABLED=true` plus their per-feature flag. `AUTONOMOUS_EXECUTION_ENABLED` and the new-entry kill switch do not disable already-open-position risk reduction. Emergency closure of a position missing required broker-side SL/TP is independently controlled by `AUTONOMOUS_EMERGENCY_EXIT_ENABLED`; it still cannot bypass `LIVE_TRADING_ENABLED=false`.

Safe progression:

1. Keep `LIVE_TRADING_ENABLED=false`, `AUTONOMOUS_EXECUTION_ENABLED=false`, and `KILL_SWITCH_ENABLED=true` through connection, model, and dry-run verification.
2. Set the intended execution flags in `.env`, set `KILL_SWITCH_ENABLED=false`, and restart.
3. Confirm `/ready`, account masking, scheduler heartbeat, durable controls, and profile blockers.

To disarm, set `AUTONOMOUS_EXECUTION_ENABLED=false` and restart. To kill immediately, set the durable kill switch through the authenticated control endpoint/MCP emergency-stop tool; also set `KILL_SWITCH_ENABLED=true` and restart when practical. A persisted emergency stop survives restart even if the environment flag is false. Clear it only as a deliberate authenticated control change while the environment flag is false.

## Database and audit operations

The default database is `storage/local_autonomous.db`. Additive SQLite initialization also creates management cycles, strict decisions, immutable previews, per-position leases, executions, and reconciliation events. No separate migration command is required.

Useful read-only inspection:

```powershell
python -c "import sqlite3; db=sqlite3.connect('storage/local_autonomous.db'); print(db.execute('select state,reason_codes_json,created_at from autonomous_decision_runs order by created_at desc limit 20').fetchall())"
python -c "import sqlite3; db=sqlite3.connect('storage/local_autonomous.db'); print(db.execute('select submission_state,reconciliation_json,updated_at from broker_submissions order by updated_at desc limit 20').fetchall())"
```

Do not edit idempotency or submission rows manually. If a submission is `unknown`, leave the original idempotency key consumed, inspect broker orders and positions, and use the stored correlation/reconciliation record. Never retry with a new key until broker state has been reconciled.

## Position-management semantics and recovery

Full close sends TradeLocker's documented `qty=0`. Partial close converts model intent into a broker-step-rounded close quantity, rejects a full-close masquerading as a reduction, rejects dust remainders, and verifies the resulting remaining quantity. Stop changes may only tighten risk and are checked against a fresh quote, tick size, and minimum distance. Take-profit changes are direction/distance checked. Break-even includes spread, configured buffer, commission, and observed swap. Trailing uses 1h ATR plus minimum-step rules and is disabled by default.

Every action binds the exact account, profile, position/order ID, symbol, market-state hash, normalized result, and immutable preview into a durable action key. A per-position SQLite lease prevents concurrent actions. Before writing, the service refreshes the target, quote, quantity, and routing. After writing, it verifies broker state. Timeouts and other ambiguous responses consume the key and remain `unknown`; restart reconciliation checks broker state without blindly resubmitting. A verified or unknown management action causes that scheduler cycle to skip new entries.

After a crash, restart with new-entry execution disabled, run `position-management`, inspect `unknown` management submissions and open positions, and allow read-only reconciliation to complete. Do not delete SQLite or retry an ambiguous action with a new key.

## Credential rotation

1. Enable the kill switch and stop the service.
2. Update the TradeLocker/OpenAI/MCP values in `.env`.
3. For `BROKER_SECRET_KEY` rotation, retain the old key through the repository's versioned key settings until stored credentials have been migrated; changing it alone makes existing encrypted rows unreadable.
4. Restart, run the TradeLocker and OpenAI verification commands, and confirm masked readiness output.
5. Re-enable execution only after dry-run verification.

## Tests

```powershell
python -m pytest -q tests/unit/test_local_autonomous.py
python -m pytest -q tests/unit/test_position_management.py
python -m pytest -q
python -m compileall -q app scripts
```

No test or verification command in this document authorizes a live order.

## Current execution scope

The inherited autonomous submission implementation remains verified-demo-account only. Local bootstrap therefore activates `demo_autonomous` only for `TRADELOCKER_ENVIRONMENT=demo`; a live account is bootstrapped read-only and will not autonomously submit. Discovered non-forex instruments can enter the whole-market universe, but deterministic execution still rejects any instrument whose broker contract, tick, quantity, margin, or pricing metadata cannot be reconciled. These are intentional fail-closed boundaries, not guessed specifications.

## Fifteen-minute execution cadence

Local autonomous startup owns the durable schedule and reconciles it from environment configuration on every restart. The default schedule runs an execution-capable decision cycle at `:00`, `:15`, `:30`, and `:45` in `APP_TIMEZONE`:

```text
AUTONOMOUS_SCHEDULE_INTERVAL_MINUTES=15
AUTONOMOUS_SCHEDULE_START_TIME=00:00
AUTONOMOUS_SCHEDULE_END_TIME=23:45
AUTONOMOUS_SCHEDULE_DAYS=monday,tuesday,wednesday,thursday,friday,saturday,sunday
AUTONOMOUS_SCHEDULE_MARKET_AWARE=true
```

This changes evaluation frequency, not risk authority. Each run still permits at most one new entry and remains subject to daily-entry, open-position, total-risk, spread, confidence, reward-to-risk, provider, broker, and kill-switch gates. Closed markets return a non-trade outcome; eligible crypto markets may still be evaluated when enabled by the strategy.
