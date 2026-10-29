"""Read-only/local-state verification commands. This script never submits an order."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.config.settings import settings
from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.services.autonomous.decision import OpenAIDecisionProvider
from app.services.autonomous.runner import AutonomousDecisionRunner
from app.services.local_autonomous import (
    _verify_openai,
    account_diagnostics,
    bootstrap_local_autonomous,
    discover_local_accounts,
    readiness_snapshot,
)
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository


def output(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


async def candle_diagnostic(symbol: str, timeframe: str) -> dict[str, object]:
    """Read one account-bound candle series without entering an order flow."""
    repository = BrokerRepository()
    accounts = repository.list_accounts(settings.local_user_id)
    selected = next((item for item in accounts if item.get("is_default_analysis")), None)
    if selected is None:
        return {"status": "blocked", "symbol": symbol, "timeframe": timeframe,
                "blocking_reason": ["selected_account_unavailable"]}
    account = repository.get_account_record(
        settings.local_user_id, alias=str(selected["account_alias"])
    )
    if account is None:
        return {"status": "blocked", "symbol": symbol, "timeframe": timeframe,
                "blocking_reason": ["selected_account_unavailable"]}
    connection = repository.get_connection(
        settings.local_user_id, str(account["connection_ref"])
    )
    if connection is None:
        return {"status": "blocked", "symbol": symbol, "timeframe": timeframe,
                "blocking_reason": ["connection_unavailable"]}
    client = TradeLockerClient(
        base_url=connection.base_url,
        username=connection.username,
        password=connection.password,
        server=connection.server,
        account_id=str(account["broker_account_id"]),
        account_number=str(account["acc_num"]),
        cache_user_id=settings.local_user_id,
        cache_connection_id=str(account["connection_ref"]),
        cache_account_record_id=str(account["public_id"]),
    )
    try:
        result = await client.get_candles(symbol, timeframe, 200, minimum_usable=50)
        canonical = result.canonical_dict()
        metadata = canonical["metadata"]
        return {
            "status": canonical["status"],
            "symbol": canonical["symbol"],
            "timeframe": canonical["requested_timeframe"],
            "provider_timeframe": canonical["provider_timeframe"],
            "rows_received": metadata["rows_received_raw"],
            "normalized_candle_count": metadata["normalized_count"],
            "usable_candle_count": metadata["usable_count"],
            "first_timestamp": metadata["oldest_timestamp"],
            "last_timestamp": metadata["newest_completed_timestamp"],
            "blocking_reason": canonical["blocking_reasons"],
            "cache_status": {
                "hit": metadata["cache_hit"],
                "fresh": metadata["cache_fresh"],
                "age_seconds": metadata["cache_age_seconds"],
                "rejection_reason": metadata["cache_rejection_reason"],
                "upstream_request_made": metadata["upstream_request_made"],
            },
            "warnings": canonical["warnings"],
        }
    except TradeLockerError as exc:
        return {
            "status": "blocked", "symbol": symbol, "timeframe": timeframe,
            "provider_timeframe": exc.details.get("provider_value_attempted"),
            "rows_received": exc.details.get("rows_received", 0),
            "normalized_candle_count": 0, "usable_candle_count": 0,
            "first_timestamp": None, "last_timestamp": None,
            "blocking_reason": [exc.code],
            "cache_status": {
                "hit": False, "fresh": False,
                "age_seconds": exc.details.get("cache_age_seconds"),
                "rejection_reason": exc.details.get("cache_rejection_reason"),
                "upstream_request_made": True,
            },
        }
    finally:
        await client.aclose()


async def decision_diagnostic(profile_ref: str) -> dict[str, object]:
    """Run only the provider call against the newest complete stored decision context."""
    source = None
    for record in ExecutionRepository().recent_decision_runs(settings.local_user_id, 50):
        if record.get("profile_ref") != profile_ref or not record.get("snapshot_id"):
            continue
        context = record.get("context") or {}
        market = context.get("market") or {}
        complete = {
            symbol: pair for symbol, pair in market.items()
            if pair.get("complete") and pair.get("timeframes")
            and all(item.get("complete") for item in pair["timeframes"].values())
        }
        if complete:
            source = record
            break
    if source is None:
        return {"status":"blocked","blocking_reason":["valid_decision_snapshot_unavailable"],
            "profile_ref":profile_ref,"order_path_invoked":False}
    context = source["context"]
    provider = OpenAIDecisionProvider(model=source.get("model_identifier") or settings.autonomous_decision_model)
    result = await provider.decide(context)
    diagnostics = result.provider_diagnostics
    return {
        "status":"failed_closed" if diagnostics.get("category") else "ok",
        "source":{"run_id":source.get("id"),"snapshot_id":source.get("snapshot_id"),
            "context_hash":source.get("context_hash"),"symbols":sorted((context.get("market") or {}).keys())},
        "provider":result.provider,"model_identifier":result.model_identifier,
        "provider_diagnostics":diagnostics,"latency_ms":result.latency_ms,"usage":result.usage,
        "decision":result.decision.model_dump(mode="json"),
        "order_path_invoked":False,
        "note":"Structured decision request only; preview, review, and submission were not invoked.",
    }


async def run(command: str, profile_ref: str | None, *, show_identifiers: bool = False,
              symbol: str = "EURUSD", timeframe: str = "1h") -> None:
    if not settings.is_local_autonomous:
        raise SystemExit("APP_MODE must be local_autonomous.")
    if command == "config":
        output({"mode": settings.app_mode, "principal": settings.local_user_id,
            "database": settings.sqlite_path, "broker_environment": settings.tradelocker_environment,
            "account": "configured" if settings.tradelocker_account_id else "missing",
            "decision_provider": settings.autonomous_decision_provider,
            "decision_model": settings.autonomous_decision_model,
            "scheduler_embedded": settings.autonomous_scheduler_embedded,
            "execution": {"live_trading": settings.live_trading_enabled,
                "autonomous": settings.autonomous_execution_enabled,
                "kill_switch": settings.kill_switch_enabled}})
        return
    if command == "accounts":
        records=await discover_local_accounts()
        output({"status":"ok","accounts":account_diagnostics(records,show_identifiers=show_identifiers)})
        return
    if command == "openai":
        await _verify_openai(settings, verify_network=True)
        output(readiness_snapshot().get("openai_model", {}))
        return
    if command == "scheduler":
        output(ScheduleRepository().worker_health())
        return
    bootstrap = await bootstrap_local_autonomous(verify_openai=False)
    if command == "candles":
        output(await candle_diagnostic(symbol, timeframe))
        return
    if command == "position-management":
        from app.services.autonomous.position_management import PositionManagementService
        wiring=PositionManagementService.wiring_status()
        current=await PositionManagementService().status(settings.local_user_id,bootstrap["profile_ref"])
        output({"status":"ready" if all(wiring["adapter_methods"].values()) else "not_ready",
            **wiring,"broker_metadata_available":bootstrap["readiness"].get("market_data",{}).get("ready",False),
            "authenticated_client_verified":current.get("broker_state_verified",False),
            "positions_observed":len(current.get("positions",[])),
            "pending_orders_observed":current.get("pending_orders",0),
            "storage_available":True,"unresolved_submissions":len(current.get("unresolved_submissions",[])),
            "note":"Read-only wiring verification; no management action was submitted."})
        return
    if command == "decision":
        selected_profile = profile_ref or bootstrap["profile_ref"]
        output(await decision_diagnostic(selected_profile))
        return
    if command == "tradelocker":
        selected=(bootstrap["readiness"].get("selected_tradelocker_account") or {})
        if show_identifiers:
            selected={**selected,"account":settings.tradelocker_account_id,
                      "acc_num":settings.tradelocker_account_number,
                      "server":settings.tradelocker_server}
        output({"account_verified": bootstrap["account_verified"],
                "profile_ref": bootstrap["profile_ref"],
                "selected_account":selected,"components": bootstrap["readiness"]})
        return
    selected_profile = profile_ref or bootstrap["profile_ref"]
    result = await AutonomousDecisionRunner().run(settings.local_user_id, selected_profile,
        f"verify-{uuid4().hex}", "local-read-only-verification", dry_run=True)
    output(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("config", "accounts", "tradelocker", "openai", "position-management", "candles", "decision", "preview", "scheduler"))
    parser.add_argument("--profile-ref")
    parser.add_argument("--show-identifiers",action="store_true")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()
    asyncio.run(run(args.command, args.profile_ref, show_identifiers=args.show_identifiers,
                    symbol=args.symbol, timeframe=args.timeframe))


if __name__ == "__main__":
    main()
