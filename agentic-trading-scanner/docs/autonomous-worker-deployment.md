# Persistent autonomous worker

Autonomous schedules are durable database records. They must be polled by one or more persistent worker services; an MCP request must never launch a temporary scheduler subprocess.

## Local development

Run the API and worker in separate terminals with the same `.env` and database:

```text
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
python -m app.jobs.autonomous_scheduler
```

Set `AUTONOMOUS_SCHEDULER_REQUIRED_FOR_READINESS=true` when `/ready` must fail unless a worker heartbeat is healthy. The worker reads durable environment toggles on every run, uses schedule leases and unique dispatch/run keys, and recovers eligible work after restart.

## Linux/systemd deployment

Install [the supplied worker unit](../deploy/systemd/agentic-forex-autonomous-worker.service) beside the separately managed API service. Replace `User`, `WorkingDirectory`, and `EnvironmentFile` for the deployment host, then run:

```text
sudo systemctl daemon-reload
sudo systemctl enable --now agentic-forex-autonomous-worker
sudo systemctl status agentic-forex-autonomous-worker
```

The API and worker must use the same application release, encrypted broker secret, server-side OpenAI configuration, and persistent database volume. With SQLite, both processes must share the exact same database file on one host. Do not put a local SQLite file on a multi-host network filesystem; use the repository's configured production database approach before scaling across hosts.

Required server-side decision settings are:

```text
AUTONOMOUS_DECISION_PROVIDER=openai
AUTONOMOUS_DECISION_MODEL=<approved model identifier>
OPENAI_API_KEY=<server secret>
```

Do not place these settings in execution profiles or expose them through MCP/frontend responses. Health is available at `/scheduler/health`, `/api/autonomous-worker-health`, and `get_autonomous_trading_status`.

