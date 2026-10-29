# Autonomous TradeLocker DEMO release gate

This gate applies only to profile-bound TradeLocker DEMO execution. It does not authorize or implement live execution. Run automated checks with a disposable test database, then run readiness against the intended profile without changing state:

```powershell
$env:SQLITE_PATH="$env:TEMP\afd-release-tests.db"
python -m pytest -q
python -m scripts.secret_leak_gate
python -m scripts.autonomous_demo_readiness --profile "<safe-profile-name>" --user "<auth-sub>"
```

The readiness command opens SQLite in read-only/query-only mode and performs broker/provider reads. It never arms a profile, changes a schedule, or submits an order. A release is GO only after every automated check and every operator verification below passes.

## Operator deployment and verification

1. Pull latest `main` and record `git rev-parse HEAD`.
2. Stop writers and make a restorable, timestamped database backup; test that the backup opens.
3. Install locked backend and frontend dependencies.
4. Apply the ordered application migrations, inspect their output, and retain the backup until verification is complete.
5. Restart the API and the dedicated scheduler/worker; confirm both use the same database and configuration.
6. Reconnect or refresh the ChatGPT app so the new protected tools appear.
7. Reauthenticate TradeLocker only if its connection check says authentication is required.
8. Verify every stored account, connection, server, environment classification, safe reference, and unique alias.
9. Confirm every live or unknown account profile is `read_only` (or disabled), never demo-enabled.
10. Fund the single intended TradeLocker demo account with the required test balance.
11. Create the execution profile and bind it explicitly to that demo account; record its safe profile and account references.
12. Set risk to 0.25% per trade, one open position, one pending order, two entries per UTC day, 3% daily loss, and 10% drawdown.
13. Run `python -m scripts.autonomous_demo_readiness --profile "<safe-profile-name>" --user "<auth-sub>"`; require `RESULT: PASS`.
14. Keep shadow mode enabled and run one autonomous cycle; verify a persisted `TRADE` decision created a preview but no broker submission.
15. Through the manual validated path, place one deliberately non-filling minimum-size pending demo order, verify exact profile/account routing, then cancel it and verify absence at the broker.
16. Through the manual validated path, place one minimum-size demo market order, verify broker-side SL and TP, then close it and verify the account is flat.
17. Repeat each submission with the same idempotency key and verify no second broker request/order occurs; reconcile any timeout as unknown before further action.
18. Create a fresh preview, enable the kill switch before submission, and verify submission is blocked. Verify the worker sees the same durable switch. After incident review, back up the database and recover locally with `python -m scripts.disable_demo_kill_switch --confirm DISABLE-DEMO-KILL-SWITCH --actor "<operator>"`; remote callers cannot disable it. Rerun readiness before continuing.
19. Disarm the profile and verify a due schedule cannot submit. Re-arm only through the dashboard, with an expiry no more than 24 hours away.
20. Enable its `America/Chicago` schedule for exactly `05:00`, `07:00`, `09:00`, `11:00`, and `13:15`.
21. Inspect and record the computed next-run UTC timestamps, including the current daylight-saving offset.
22. Monitor the first scheduled shadow run through dispatch completion and TradeLocker history; prove it fired exactly once before leaving the system unattended.

## Rollback and emergency procedure

1. Disable all autonomous schedules.
2. Disarm every autonomous profile.
3. Enable the durable kill switch from the authenticated dashboard/API. Do not attempt remote disable.
4. Using only the validated profile-scoped risk-reducing action path, cancel demo pending orders and reconcile each cancellation.
5. Using the same path, close demo positions and verify the demo account is flat.
6. Stop the scheduler/worker, retain logs and the database for investigation, and restore the pre-migration backup only after confirming schema/application compatibility.

Never use rollback as permission to act on a live or unknown account.

## Automated release matrix

The full suite includes routing and tenant isolation (`test_multi_account_storage.py`), manual risk-reducing actions (`test_demo_actions.py`), atomic protected broker writes (`test_tradelocker_client.py` and `test_autonomous_execution.py`), decision persistence (`test_autonomous_decision_runner.py`), durable worker locking and timing (`test_autonomous_scheduler.py`), MCP authorization (`test_mcp_server.py`), and the operational safety regressions under `tests/release_gate`. The secret regression is `python -m scripts.secret_leak_gate` and must also pass after the frontend production build.
