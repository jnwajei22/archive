from __future__ import annotations

from typing import Any, Callable

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.config.settings import Settings, settings
from app.storage.brokers import BrokerRepository, BrokerStorageError
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository
from app.jobs.autonomous_scheduler import AutonomousScheduleService


class LocalAutonomousStartupError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_readiness: dict[str, dict[str, Any]] = {}


def execution_arming_status(source: Settings = settings) -> dict[str, Any]:
    """Compatibility view for clients that still display the retired arming fields.

    New-entry authority is durable and comes from the environment flags, per-owner
    autonomous controls, profile state, and kill switches. It must not silently
    expire based on how long the Python process has been running.
    """
    enabled = source.autonomous_execution_enabled
    return {"armed": enabled, "armed_until": None,
            "reason": None if enabled else "autonomous_execution_disabled",
            "legacy_arming_deprecated": True}


def execution_gate_reason(source: Settings = settings, *, durable_kill_switch: bool = False) -> str | None:
    if not source.live_trading_enabled:
        return "live_trading_disabled"
    if not source.autonomous_execution_enabled:
        return "autonomous_execution_disabled"
    if source.kill_switch_enabled or durable_kill_switch:
        return "kill_switch_enabled"
    return None


def readiness_snapshot() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in _readiness.items()}


def _component(name: str, ready: bool, detail: str | None = None, **safe: Any) -> None:
    _readiness[name] = {"ready": ready, "status": "ready" if ready else "not_ready",
                        **({"detail": detail} if detail else {}), **safe}


async def bootstrap_local_autonomous(
    source: Settings = settings,
    *,
    repository: BrokerRepository | None = None,
    client_factory: Callable[..., TradeLockerClient] = TradeLockerClient,
    verify_openai: bool = True,
) -> dict[str, Any]:
    """Authenticate, uniquely bind the configured account, and seed local ownership."""
    if not source.is_local_autonomous:
        return {"mode": source.app_mode, "skipped": True}
    _readiness.clear()
    _component("application_mode", True, mode="local_autonomous")
    repo = repository or BrokerRepository()
    _component("database", True)
    try:
        async with client_factory(base_url=source.tradelocker_base_url,
            username=source.tradelocker_username, password=source.tradelocker_password,
            server=source.tradelocker_server, account_id=None, account_number=None) as client:
            discovered = await client.get_accounts()
    except (TradeLockerError, OSError) as exc:
        _component("tradelocker_authentication", False, "TradeLocker authentication failed.")
        raise LocalAutonomousStartupError("tradelocker_authentication_failed",
            "TradeLocker authentication failed; verify the configured login and server.") from None
    _component("tradelocker_authentication", True)
    selected = resolve_discovered_account(discovered, source)
    discovered_acc_num = str(selected["accNum"]).strip()
    source.tradelocker_account_number = discovered_acc_num
    connection = repo.save_connection(source.local_user_id, base_url=source.tradelocker_base_url,
        username=source.tradelocker_username or "", password=source.tradelocker_password or "",
        server=source.tradelocker_server or "", environment=source.tradelocker_environment,
        email=source.tradelocker_email, label="local-autonomous")
    repo.sync_accounts(source.local_user_id, connection.connection_ref, discovered)
    if not repo.select_account(source.local_user_id, str(source.tradelocker_account_id),
                               str(source.tradelocker_account_number), connection.connection_ref):
        raise LocalAutonomousStartupError("account_selection_failed",
            "The verified TradeLocker account could not be selected.")
    _component("selected_tradelocker_account", True,
               account=mask_identifier(str(source.tradelocker_account_id)),
               acc_num=mask_identifier(discovered_acc_num), server=normalize_server(source.tradelocker_server),
               environment=source.tradelocker_environment,
               is_demo=source.tradelocker_environment == "demo")
    try:
        async with client_factory(base_url=source.tradelocker_base_url,
            username=source.tradelocker_username, password=source.tradelocker_password,
            server=source.tradelocker_server, account_id=source.tradelocker_account_id,
            account_number=source.tradelocker_account_number) as client:
            instruments = await client.get_symbols()
        _component("market_data", True, instruments_available=bool(instruments))
    except (TradeLockerError, OSError):
        _component("market_data", False, "TradeLocker instrument discovery failed.")
        raise LocalAutonomousStartupError("market_data_verification_failed",
            "TradeLocker market-data verification failed.") from None
    profile = ensure_local_profile(repo, source)
    execution_repo = ExecutionRepository(source.sqlite_path)
    prior_controls = execution_repo.get_autonomous_controls(source.local_user_id)
    controls = execution_repo.update_autonomous_controls(source.local_user_id, {
        # A persisted emergency stop survives restart. Clearing it must be an
        # explicit authenticated control change while the environment flag is false.
        "global_autonomous_kill_switch": source.kill_switch_enabled or prior_controls["global_autonomous_kill_switch"],
        "demo_autonomous_enabled": source.autonomous_execution_enabled,
        "live_autonomous_enabled": source.autonomous_execution_enabled,
    }, updated_by=source.local_user_id, source="local_environment",
       reason="Synchronized from local autonomous environment", audit_unchanged=False)
    schedule_repo = ScheduleRepository(source.sqlite_path)
    schedule_service = AutonomousScheduleService(schedules=schedule_repo, brokers=repo,
                                                  execution=execution_repo)
    # Local cadence configuration is authoritative. Re-save on every startup so an
    # existing hourly schedule is migrated, while preserving an intentional pause.
    existing = schedule_repo.get_profile_schedule(source.local_user_id, profile["public_id"])
    schedule = schedule_service.save(source.local_user_id, profile["public_id"],
        timezone_name=source.app_timezone,
        recurrence={"type": "recurring_interval",
                    "start_time": source.autonomous_schedule_start_time,
                    "end_time": source.autonomous_schedule_end_time,
                    "interval_minutes": source.autonomous_schedule_interval_minutes,
                    "days": source.schedule_days,
                    "market_aware": source.autonomous_schedule_market_aware},
        enabled=bool(existing.get("enabled", True)) if existing else True)
    _component("scheduler_configuration", True, embedded=source.autonomous_scheduler_embedded,
        schedule_id=schedule["id"], schedule_type=schedule["recurrence"]["type"],
        interval_minutes=schedule["recurrence"].get("interval_minutes"),
        next_run_at=schedule.get("next_run_at"))
    from app.services.autonomous.position_management import PositionManagementService
    wiring=PositionManagementService.wiring_status()
    _component("position_management_wiring",all(wiring["adapter_methods"].values()),
        scheduler_phase=wiring["scheduler_phase"],features=wiring["features"])
    await _verify_openai(source, verify_network=verify_openai)
    _component("strategy_configuration", True, strategy_name=source.autonomous_strategy_name)
    _component("risk_configuration", True)
    arming = execution_arming_status(source)
    gate = execution_gate_reason(source,
        durable_kill_switch=controls["global_autonomous_kill_switch"])
    _component("execution_permission", gate is None, live_trading=source.live_trading_enabled,
        autonomous_execution=source.autonomous_execution_enabled,
        kill_switch=source.kill_switch_enabled or controls["global_autonomous_kill_switch"],
        blocking_reason=gate, **arming)
    return {"mode": "local_autonomous", "principal": source.local_user_id,
            "profile_ref": profile["public_id"], "account_verified": True,
            "controls": controls, "readiness": readiness_snapshot()}


def ensure_local_profile(repo: BrokerRepository, source: Settings) -> dict[str, Any]:
    accounts = repo.list_accounts(source.local_user_id)
    selected = next((item for item in accounts if item.get("is_default_analysis")), None)
    if selected is None:
        raise LocalAutonomousStartupError("local_account_not_selected",
            "The local account record is not selected.")
    profiles = repo.list_profiles(source.local_user_id)
    profile = next((item for item in profiles if item["account_id"] == selected["account_id"]
                    and item["name"] == source.autonomous_strategy_name), None)
    if profile is None:
        profile = repo.create_profile(source.local_user_id, name=source.autonomous_strategy_name,
            account_ref=selected["account_id"], execution_mode="read_only")
    risk = min(source.autonomous_risk_per_trade_percent, 1.0)
    groups: list[str] = []
    for asset_class in source.allowed_asset_classes:
        groups.extend(("forex_majors", "forex_minors", "forex_exotics")
                      if asset_class == "forex" else (asset_class,))
    universe = ({"mode": "custom", "included_instrument_ids": source.allowed_symbols,
                 "excluded_instrument_ids": source.blocked_symbols}
                if source.allowed_symbols else
                {"mode": "groups", "groups": list(dict.fromkeys(groups)),
                 "excluded_instrument_ids": source.blocked_symbols})
    patch = {
        "trading_policy": {"mode": "adaptive", "preset_id": None,
                           "minimum_confidence": source.autonomous_minimum_confidence},
        "market_universe": universe,
        "risk_policy": {"mode": "fixed", "fixed_risk_pct": risk,
            "base_risk_pct": risk, "minimum_risk_pct": min(0.1, risk),
            "maximum_risk_pct": max(0.5, risk),
            "maximum_total_open_risk_pct": source.autonomous_max_total_risk_percent,
            "daily_loss_limit_pct": source.autonomous_max_daily_loss_percent,
            "drawdown_cutoff_pct": source.autonomous_max_drawdown_percent,
            "maximum_open_positions": source.autonomous_max_open_positions,
            "maximum_pending_entry_orders": source.autonomous_max_open_positions,
            "maximum_new_entries_per_day": source.autonomous_max_trades_per_day},
        "exit_policy": {"stop_loss": {"enabled": source.autonomous_require_stop_loss},
            "take_profit": {"enabled": source.autonomous_require_take_profit,
                "minimum_reward_to_risk": source.autonomous_minimum_risk_reward,
                "target_reward_to_risk": source.autonomous_minimum_risk_reward}},
        "enabled": True,
    }
    updated = repo.update_profile_v2(source.local_user_id, profile["public_id"], patch)
    repo.update_profile(source.local_user_id, profile["public_id"],
        execution_mode="demo_autonomous" if source.tradelocker_environment == "demo" else "read_only",
        decision_provider=source.autonomous_decision_provider,
        model_identifier=source.autonomous_decision_model,
        minimum_confidence=source.autonomous_minimum_confidence)
    repo.configure_local_runtime_profile(source.local_user_id, profile["public_id"],
        cooldown_minutes=source.autonomous_cooldown_minutes)
    return repo.get_profile(source.local_user_id, profile["public_id"]) or updated or profile


async def _verify_openai(source: Settings, *, verify_network: bool) -> None:
    if not (source.openai_api_key or "").strip():
        _component("openai_api_key", False, "OPENAI_API_KEY is not configured.")
        _component("openai_model", False, "OpenAI model accessibility was not checked.")
        return
    _component("openai_api_key", True)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=source.openai_api_key,
            timeout=source.autonomous_decision_timeout_seconds, max_retries=0)
        # Constructing the typed provider verifies structured-output initialization.
        from app.services.autonomous.decision import OpenAIDecisionProvider
        OpenAIDecisionProvider(api_key=source.openai_api_key, model=source.autonomous_decision_model)
        if verify_network:
            await client.models.retrieve(source.autonomous_decision_model)
        await client.close()
    except Exception:
        _component("openai_model", False, "Configured OpenAI model is inaccessible.")
        if verify_network:
            raise LocalAutonomousStartupError("openai_model_verification_failed",
                "OpenAI model verification failed; verify the redacted model ID and API project access.") from None
        return
    _component("openai_model", True, model=source.autonomous_decision_model)


def mask_identifier(value: str) -> str:
    normalized=str(value)
    if len(normalized)<=4:return "*"*len(normalized)
    return "*"*(len(normalized)-4)+normalized[-4:]


def normalize_server(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _account_environment(record: dict[str, Any], fallback: str) -> str:
    raw = str(record.get("environment") or "").strip().lower()
    if raw in {"demo", "live"}:
        return raw
    demo = record.get("isDemo", record.get("demo"))
    if isinstance(demo, bool):
        return "demo" if demo else "live"
    return fallback.strip().lower()


def resolve_discovered_account(discovered: Any, source: Settings = settings) -> dict[str, Any]:
    records = discovered.get("accounts", []) if isinstance(discovered, dict) else []
    records = [item for item in records if isinstance(item, dict)]
    if not records:
        raise LocalAutonomousStartupError("no_accounts_returned", "TradeLocker returned no accounts.")
    configured_id = str(source.tradelocker_account_id or "").strip()
    if not configured_id:
        raise LocalAutonomousStartupError("account_id_missing", "TRADELOCKER_ACCOUNT_ID is required.")
    matches = [item for item in records if str(item.get("accountId", "")).strip() == configured_id]
    if not matches:
        raise LocalAutonomousStartupError("account_id_not_found",
            f"Configured account ID {mask_identifier(configured_id)} was not found.")
    if len(matches) > 1:
        raise LocalAutonomousStartupError("account_id_ambiguous",
            f"Multiple TradeLocker accounts matched {mask_identifier(configured_id)}.")
    selected = matches[0]
    discovered_number = str(selected.get("accNum") or "").strip()
    if not discovered_number:
        raise LocalAutonomousStartupError("account_number_missing_from_discovery",
            "The selected TradeLocker account did not provide accNum.")
    asserted_number = str(source.tradelocker_account_number or "").strip()
    if asserted_number and asserted_number != discovered_number:
        raise LocalAutonomousStartupError("account_number_conflict",
            "The optional configured account-number assertion conflicted with TradeLocker discovery.")
    discovered_server = normalize_server(selected.get("server"))
    configured_server = normalize_server(source.tradelocker_server)
    if discovered_server and configured_server and discovered_server != configured_server:
        raise LocalAutonomousStartupError("server_conflict",
            "The configured server conflicted with the selected TradeLocker account.")
    discovered_environment = _account_environment(selected, source.tradelocker_environment)
    configured_environment = source.tradelocker_environment.strip().lower()
    if discovered_environment and configured_environment and discovered_environment != configured_environment:
        raise LocalAutonomousStartupError("environment_conflict",
            "The configured environment conflicted with the selected TradeLocker account.")
    return {**selected, "accNum": discovered_number,
            "server": discovered_server or configured_server,
            "environment": discovered_environment,
            "isDemo": discovered_environment == "demo"}


async def discover_local_accounts(source: Settings = settings,
    *, client_factory: Callable[..., TradeLockerClient] = TradeLockerClient) -> list[dict[str, Any]]:
    try:
        async with client_factory(base_url=source.tradelocker_base_url,
            username=source.tradelocker_username, password=source.tradelocker_password,
            server=source.tradelocker_server, account_id=None, account_number=None) as client:
            payload = await client.get_accounts()
    except (TradeLockerError, OSError):
        raise LocalAutonomousStartupError("tradelocker_authentication_failed",
            "TradeLocker authentication failed.") from None
    records = payload.get("accounts", []) if isinstance(payload, dict) else []
    if not records:
        raise LocalAutonomousStartupError("no_accounts_returned", "TradeLocker returned no accounts.")
    return [item for item in records if isinstance(item, dict)]


def account_diagnostics(records: list[dict[str, Any]], source: Settings = settings,
                        *, show_identifiers: bool = False) -> list[dict[str, Any]]:
    results=[]
    for record in records:
        account_id=str(record.get("accountId") or "")
        environment=_account_environment(record, source.tradelocker_environment)
        server=normalize_server(record.get("server") or source.tradelocker_server)
        results.append({
            "account_id":account_id if show_identifiers else mask_identifier(account_id),
            "acc_num":str(record.get("accNum") or "") if show_identifiers else mask_identifier(str(record.get("accNum") or "")),
            "server":str(record.get("server") or source.tradelocker_server).strip() if show_identifiers else server,
            "environment":environment,"is_demo":environment=="demo",
            "currency":record.get("currency"),"name":record.get("name"),
            "balance":record.get("balance",record.get("projectedBalance")),
        })
    return results
