# Autonomous demo decision runner

The runner is deliberately profile-bound and demo-only. It cannot choose or override an account, quantity, risk percentage, leverage, or routing target. Those values come from an authenticated execution profile and the same deterministic preview/submission service used by Demo Manual execution.

## Enablement

1. Create a profile on a TradeLocker account classified and re-verified as demo.
2. Enable the durable **Demo Autonomous Trading** control and confirm the profile, provider, schedule, and risk limits.
3. Keep shadow mode enabled for initial verification. A shadow TRADE creates and records a validated preview but never submits it.
4. Configure `OPENAI_API_KEY` only on the backend and select the `openai` provider in server-side profile configuration. `no_trade` is the fail-closed default.
5. Disable the global kill switch only through protected operator controls. MCP callers may start an eligible environment or enable the emergency stop, but cannot alter local account, strategy, or risk configuration.

The durable scheduler uses the configured IANA timezone and local times to trigger the same idempotent runner. An authenticated caller may also trigger one run with `run_autonomous_demo_profile(profile_ref, run_key, trigger_reason)`. Reusing a run key returns the existing result, and the database permits only one active run per user/profile.

## Safety gates

Every run rechecks profile ownership, durable environment controls, verified demo routing, weekend/session restrictions, provider availability, provider/news freshness, account funds, position/order limits, daily-entry limits, daily loss, drawdown, and the post-loss cooldown. Model output is strict-schema data only. It is validated again before the immutable preview is created; submission uses that preview and a runner-derived idempotency key.

Provider failure, refusal, malformed output, oversized context, incomplete candles, stale news, or any ambiguous broker state fails closed as `NO_TRADE`, `BLOCKED`, or `ERROR`. Live accounts are never eligible.

## Inspection and recovery

- Status tools show durable environment controls, provider readiness, schedule state, and blockers.
- Recent Demo Executions includes autonomous decision states.
- MCP read tools expose status, bounded snapshots, and durable run results without credentials or broker account identifiers.
- Stopping demo autonomy immediately blocks new cycles while preserving schedules, orders, and positions.
- If a broker submission becomes `unknown`, do not retry with a new key; inspect the recorded execution and reconcile the demo account first.
