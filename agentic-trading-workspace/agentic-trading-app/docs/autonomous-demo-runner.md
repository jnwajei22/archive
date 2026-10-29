# Autonomous demo decision runner

The runner is deliberately profile-bound and demo-only. It cannot choose or override an account, quantity, risk percentage, leverage, or routing target. Those values come from an authenticated execution profile and the same deterministic preview/submission service used by Demo Manual execution.

## Enablement

1. Create a profile on a TradeLocker account classified and re-verified as demo.
2. In the authenticated dashboard, choose **Arm Demo Autonomy** and confirm the provider, shadow/submit behavior, risk limits, and 24-hour maximum expiry.
3. Keep shadow mode enabled for initial verification. A shadow TRADE creates and records a validated preview but never submits it.
4. Configure `OPENAI_API_KEY` only on the backend and select the `openai` provider when arming. `no_trade` is the fail-closed default.
5. Disable the global kill switch only through the existing protected operator configuration. MCP callers can enable it, but cannot disable it or arm/reconfigure a profile.

The durable scheduler uses the configured IANA timezone and local times to trigger the same idempotent runner. An authenticated caller may also trigger one run with `run_autonomous_demo_profile(profile_ref, run_key, trigger_reason)`. Reusing a run key returns the existing result, and the database permits only one active run per user/profile.

## Safety gates

Every run rechecks profile ownership, arming expiry, verified demo routing, weekend/session restrictions, provider availability, provider/news freshness, account funds, one-position/one-order limits, two new entries per day, 3% daily loss, 10% drawdown, and the 60-minute post-loss cooldown. Model output is strict-schema data only. It is validated again before the immutable preview is created; submission uses that preview and a runner-derived idempotency key.

Provider failure, refusal, malformed output, oversized context, incomplete candles, stale news, or any ambiguous broker state fails closed as `NO_TRADE`, `BLOCKED`, or `ERROR`. Live accounts are never eligible.

## Inspection and recovery

- Dashboard profile status shows arming, expiry, provider, shadow mode, and blockers.
- Recent Demo Executions includes autonomous decision states.
- MCP read tools expose status, bounded snapshots, and durable run results without credentials or broker account identifiers.
- Disarming immediately removes autonomous authority and returns the profile to Demo Manual mode.
- If a broker submission becomes `unknown`, do not retry with a new key; inspect the recorded execution and reconcile the demo account first.
