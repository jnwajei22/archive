# Autonomous demo scheduler operations

The scheduler is a durable dispatcher for the existing `AutonomousDecisionRunner`. It never analyzes markets, sizes orders, arms profiles, changes account bindings or risk, or disables the kill switch. Each local-time occurrence becomes one dispatch with the deterministic key `scheduled:{profile_ref}:{scheduled UTC timestamp}`. The runner and broker-submission idempotency layers remain authoritative.

## Schema migration

Back up `storage/app.db`, deploy the code, then start either the API or worker once. `ScheduleRepository` applies idempotent `CREATE TABLE/INDEX IF NOT EXISTS` migrations for:

- `autonomous_schedules`
- `autonomous_schedule_dispatches`
- `autonomous_worker_heartbeats`

No separate SQLite migration command is required. Confirm with:

```powershell
python -c "from app.storage.schedules import ScheduleRepository; print(ScheduleRepository().worker_health())"
```

The application currently uses its SQLite repositories even when `DATABASE_URL` is present. SQLite locking uses `BEGIN IMMEDIATE` and leases, which is safe for one scheduler process sharing a local database file. Do not run multiple scheduler processes against copied databases, network filesystems, or separate container filesystems. A future PostgreSQL repository must claim due rows with `FOR UPDATE SKIP LOCKED` (or transaction-scoped advisory locks) before horizontally scaling workers.

## Configuration

Set the variables documented in `.env.example`. Important scheduler defaults are a 30-second poll, 180-second lease, batch size 20, two retries, 30/60-second exponential retry delays capped at 300 seconds, and a 120-second heartbeat threshold. Keep `AUTONOMOUS_SCHEDULER_REQUIRED_FOR_READINESS=false` until the worker is deployed and healthy.

Profiles and schedules are never automatically enabled. Create a schedule from the authenticated dashboard. The initial schedule is `05:00, 07:00, 09:00, 11:00, 13:15` in `America/Chicago`, with canonical occurrences stored in UTC. Arming still expires after at most 24 hours.

## Start commands

Development, two processes (recommended):

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.jobs.autonomous_scheduler
```

Development, one API process with an embedded durable worker:

```powershell
$env:AUTONOMOUS_SCHEDULER_EMBEDDED="true"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Production process separation:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.jobs.autonomous_scheduler
```

Run exactly one SQLite scheduler worker. Set `AUTONOMOUS_SCHEDULER_EMBEDDED=false` in production so an API replica cannot accidentally start another worker. Once healthy, optionally set `AUTONOMOUS_SCHEDULER_REQUIRED_FOR_READINESS=true` on the API.

## Health, logs, and reports

- `GET /health`: API liveness.
- `GET /ready`: API readiness plus scheduler state; returns 503 when scheduler readiness is required and stale.
- `GET /scheduler/health`: non-secret worker counts.
- Dashboard: worker heartbeat, local/UTC next and last runs, pause/resume/delete, disarm, kill-switch enable, and recent outcomes.
- MCP: `list_autonomous_schedules`, `get_autonomous_schedule_status`, `list_recent_autonomous_runs`, and `get_autonomous_daily_summary` are read-only.

Logs go to the process supervisor's stdout/stderr capture (or the configured application log collector). Structured messages cover heartbeat polls, claims, reclaim after lease expiry, misfires, lock contention, duplicate suppression, retry count, provider latency, outcome, and safe profile references. Credentials and connection payloads are never logged.

ChatGPT Scheduled Tasks are optional. They should call only `run_autonomous_demo_profile` with a unique run key; they must not call preview/submit tools individually. Backend scheduling is the primary unattended path.

## Retry and restart behavior

Only failures categorized as transient and occurring before a preview/execution may retry. Retries reuse the same dispatch and run key. `NO_TRADE`/`BLOCKED` policy outcomes, broker rejection, protection failure, and any broker timeout or unknown submission state are final for automatic scheduling. Unknown broker state requires reconciliation and is never blindly resubmitted.

On restart, an unclaimed due schedule is claimed normally. An expired dispatch lease is reclaimed with its original run key. A completed runner record is returned by idempotency without another order. Pre-submit stale work may resume; work at or after preview/submission is not replayed automatically.

Runs later than the configured maximum lateness are recorded once as `misfired` with `maximum_lateness_exceeded`, then `next_run_at` advances beyond the current time so missed occurrences never bunch.

## Disable, rollback, and emergency procedure

Normal disable:

1. Pause schedules in the dashboard.
2. Disarm every autonomous profile.
3. Stop the scheduler process (and set `AUTONOMOUS_SCHEDULER_EMBEDDED=false`).
4. Keep the API running for inspection and reconciliation.

Emergency:

1. Select **Enable Kill Switch** in the dashboard, or set `KILL_SWITCH_ENABLED=true` and restart services.
2. Disarm profiles and pause schedules.
3. Stop the scheduler worker.
4. Inspect unknown/protection-failure executions before any later re-enable operation.

Rollback the application code only after stopping the worker. The new tables are additive and may remain in place; do not drop them during an incident. Restore the database backup only when intentionally discarding all schedules, dispatch audit history, and post-backup execution state.
