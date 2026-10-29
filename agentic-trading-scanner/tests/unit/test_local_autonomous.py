from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from fastapi import HTTPException

from app.auth.identity import get_current_user_sub
from app.config.settings import Settings, settings
from app.mcp.auth import MCPAuthMiddleware
from app.jobs.autonomous_scheduler import AutonomousScheduleService
from app.services.local_autonomous import (LocalAutonomousStartupError, account_diagnostics,
    bootstrap_local_autonomous, execution_arming_status, execution_gate_reason, resolve_discovered_account)
from app.storage.brokers import BrokerRepository
from app.storage.schedules import ScheduleRepository
from app.api.routes.platform import _require_local_admin


def local_settings(tmp_path, **overrides):
    values = dict(app_mode="local_autonomous", public_base_url="https://public.example.test",
        local_mcp_api_token="mcp-test-secret", local_user_id="local-owner",
        database_url=None, sqlite_path=str(tmp_path / "local.db"), broker_secret_key="cipher-secret",
        tradelocker_email="owner@example.test", tradelocker_password="broker-password",
        tradelocker_server="server", tradelocker_account_id="42", tradelocker_account_number="7001",
        autonomous_strategy_json='{"version":1}', autonomous_decision_provider="openai")
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_local_mode_does_not_require_auth0_settings(tmp_path):
    source = local_settings(tmp_path, auth_issuer=None, auth_jwks_url=None, mcp_require_oauth=True)
    assert source.mcp_require_oauth is False
    assert source.tradelocker_username == "owner@example.test"


def test_account_number_is_optional_and_discovered_by_exact_account_id(tmp_path):
    source=local_settings(tmp_path,tradelocker_account_number=None,tradelocker_server="  herofx  ")
    selected=resolve_discovered_account({"accounts":[
        {"accountId":"other","accNum":"1","server":"HEROFX","isDemo":True},
        {"accountId":"42","accNum":"9","server":" HeroFX ","environment":"demo"},
    ]},source)
    assert selected["accountId"]=="42" and selected["accNum"]=="9"
    assert selected["server"]=="HEROFX"


@pytest.mark.parametrize(("accounts","override","code"),[
    ([],{},"no_accounts_returned"),
    ([{"accountId":"7","accNum":"1"}],{},"account_id_not_found"),
    ([{"accountId":"42"}],{},"account_number_missing_from_discovery"),
    ([{"accountId":"42","accNum":"2"}],{"tradelocker_account_number":"1"},"account_number_conflict"),
    ([{"accountId":"42","accNum":"1","server":"Other"}],{"tradelocker_account_number":None},"server_conflict"),
    ([{"accountId":"42","accNum":"1","environment":"live"}],{"tradelocker_account_number":None},"environment_conflict"),
])
def test_account_discovery_failure_codes_are_precise(tmp_path,accounts,override,code):
    source=local_settings(tmp_path,**override)
    with pytest.raises(LocalAutonomousStartupError) as error:
        resolve_discovered_account({"accounts":accounts},source)
    assert error.value.code==code


def test_account_diagnostics_mask_identifiers_unless_explicit(tmp_path):
    source=local_settings(tmp_path)
    records=[{"accountId":"2328008","accNum":"17","server":" herofx ","isDemo":True}]
    safe=account_diagnostics(records,source)
    shown=account_diagnostics(records,source,show_identifiers=True)
    assert safe[0]["account_id"]=="***8008" and safe[0]["acc_num"]=="**"
    assert shown[0]["account_id"]=="2328008" and shown[0]["server"]=="herofx"


def test_production_preserves_oauth_setting():
    source = Settings(_env_file=None, app_mode="production", mcp_require_oauth=True,
                      public_base_url="https://public.example.test")
    assert source.mcp_require_oauth is True


def test_invalid_strategy_json_fails_startup(tmp_path):
    with pytest.raises(ValidationError, match="AUTONOMOUS_STRATEGY_JSON"):
        local_settings(tmp_path, autonomous_strategy_json="not-json")


def test_public_local_mcp_without_token_fails_closed(tmp_path):
    with pytest.raises(ValidationError, match="LOCAL_MCP_API_TOKEN"):
        local_settings(tmp_path, local_mcp_api_token=None)


def test_missing_tradelocker_password_fails_startup(tmp_path):
    with pytest.raises(ValidationError, match="TRADELOCKER_PASSWORD"):
        local_settings(tmp_path, tradelocker_password=None)


def test_local_identity_ignores_caller_claims(monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    monkeypatch.setattr(settings, "local_user_id", "fixed-owner")
    assert get_current_user_sub() == "fixed-owner"


def test_static_mcp_bearer_token_sets_fixed_principal(monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    monkeypatch.setattr(settings, "local_user_id", "fixed-owner")
    monkeypatch.setattr(settings, "local_mcp_api_token", "expected-token")
    monkeypatch.setattr(settings, "allow_unauthenticated_public_mcp", False)

    async def endpoint(request):
        return JSONResponse({"user": get_current_user_sub()})

    app = MCPAuthMiddleware(Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])]))
    with TestClient(app) as client:
        assert client.post("/mcp").status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.post("/mcp", headers={"Authorization": "Bearer expected-token"})
    assert response.status_code == 200
    assert response.json() == {"user": "fixed-owner"}


class FakeClient:
    accounts = []
    def __init__(self, **kwargs): self.kwargs = kwargs
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def get_accounts(self): return {"accounts": self.accounts}
    async def get_symbols(self): return {"instruments": [{"id": "1"}]}


def test_ambiguous_account_selection_fails_without_persisting(tmp_path):
    source = local_settings(tmp_path)
    FakeClient.accounts = [
        {"accountId": "42", "accNum": "7001"},
        {"accountId": "42", "accNum": "7001"},
    ]
    repo = BrokerRepository(source.sqlite_path, source.broker_secret_key)
    with pytest.raises(LocalAutonomousStartupError, match="Multiple TradeLocker accounts matched"):
        asyncio.run(bootstrap_local_autonomous(source, repository=repo,
                    client_factory=FakeClient, verify_openai=False))
    assert repo.list_connections(source.local_user_id) == []


def test_missing_openai_key_is_a_redacted_readiness_failure(tmp_path, monkeypatch):
    source = local_settings(tmp_path, openai_api_key=None)
    FakeClient.accounts = [{"accountId": "42", "accNum": "7001", "status": "active"}]
    repo = BrokerRepository(source.sqlite_path, source.broker_secret_key)
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    result = asyncio.run(bootstrap_local_autonomous(source, repository=repo,
        client_factory=FakeClient, verify_openai=False))
    assert result["readiness"]["openai_api_key"]["ready"] is False
    assert "key" not in str(result["readiness"]).lower().replace("api_key", "")


@pytest.mark.parametrize(("overrides", "reason"), [
    ({"live_trading_enabled": False, "autonomous_execution_enabled": True, "kill_switch_enabled": False}, "live_trading_disabled"),
    ({"live_trading_enabled": True, "autonomous_execution_enabled": False, "kill_switch_enabled": False}, "autonomous_execution_disabled"),
    ({"live_trading_enabled": True, "autonomous_execution_enabled": True, "kill_switch_enabled": True}, "kill_switch_enabled"),
])
def test_each_execution_gate_fails_closed(tmp_path, overrides, reason):
    assert execution_gate_reason(local_settings(tmp_path, **overrides)) == reason


def test_all_execution_gates_allow_workflow(tmp_path):
    source = local_settings(tmp_path, live_trading_enabled=True,
        autonomous_execution_enabled=True, kill_switch_enabled=False)
    assert execution_gate_reason(source) is None
    assert execution_gate_reason(source, durable_kill_switch=True) == "kill_switch_enabled"


def test_legacy_arming_window_is_deprecated_and_never_time_expires(tmp_path):
    source = local_settings(tmp_path, live_trading_enabled=True,
        autonomous_execution_enabled=True, kill_switch_enabled=False,
        autonomous_max_arming_hours=1)
    status = execution_arming_status(source)
    assert status == {"armed": True, "armed_until": None, "reason": None,
                      "legacy_arming_deprecated": True}
    assert execution_gate_reason(source) is None


def test_remote_account_and_risk_changes_are_locked(monkeypatch):
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    with pytest.raises(HTTPException) as exc:
        _require_local_admin(False, "Account selection")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "local_configuration_locked"


def test_local_bootstrap_migrates_existing_hourly_schedule_to_configured_15_minutes(tmp_path, monkeypatch):
    source = local_settings(tmp_path, autonomous_schedule_interval_minutes=15,
        autonomous_schedule_start_time="00:00", autonomous_schedule_end_time="23:45",
        autonomous_execution_enabled=True, live_trading_enabled=True, kill_switch_enabled=False)
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    FakeClient.accounts = [{"accountId": "42", "accNum": "7001", "status": "active"}]
    repo = BrokerRepository(source.sqlite_path, source.broker_secret_key)
    first = asyncio.run(bootstrap_local_autonomous(source, repository=repo,
        client_factory=FakeClient, verify_openai=False))
    profile_ref = first["profile_ref"]
    schedules = ScheduleRepository(source.sqlite_path)
    hourly = AutonomousScheduleService(schedules=schedules, brokers=repo).save(
        source.local_user_id, profile_ref, timezone_name=source.app_timezone,
        recurrence={"type": "recurring_interval", "start_time": "00:00",
            "end_time": "23:00", "interval_minutes": 60,
            "days": source.schedule_days, "market_aware": True}, enabled=True)

    second = asyncio.run(bootstrap_local_autonomous(source, repository=repo,
        client_factory=FakeClient, verify_openai=False))
    stored = schedules.get_profile_schedule(source.local_user_id, profile_ref)
    recurrence = stored["expression"]["recurrence"]

    assert stored["id"] == hourly["id"]
    assert recurrence["interval_minutes"] == 15
    assert recurrence["start_time"] == "00:00" and recurrence["end_time"] == "23:45"
    assert len(recurrence["times"]) == 96
    assert recurrence["times"][:4] == ["00:00", "00:15", "00:30", "00:45"]
    assert recurrence["times"][-1] == "23:45"
    assert second["readiness"]["scheduler_configuration"]["interval_minutes"] == 15


def test_schedule_settings_reject_misaligned_window(tmp_path):
    with pytest.raises(ValidationError, match="AUTONOMOUS_SCHEDULE_END_TIME"):
        local_settings(tmp_path, autonomous_schedule_interval_minutes=30,
            autonomous_schedule_start_time="00:00", autonomous_schedule_end_time="23:45")


def test_local_bootstrap_migrates_cadence_without_unpausing_schedule(tmp_path, monkeypatch):
    source = local_settings(tmp_path, autonomous_schedule_interval_minutes=15,
        autonomous_schedule_start_time="00:00", autonomous_schedule_end_time="23:45")
    monkeypatch.setattr(settings, "app_mode", "local_autonomous")
    FakeClient.accounts = [{"accountId": "42", "accNum": "7001", "status": "active"}]
    repo = BrokerRepository(source.sqlite_path, source.broker_secret_key)
    first = asyncio.run(bootstrap_local_autonomous(source, repository=repo,
        client_factory=FakeClient, verify_openai=False))
    schedules = ScheduleRepository(source.sqlite_path)
    stored = schedules.get_profile_schedule(source.local_user_id, first["profile_ref"])
    schedules.set_enabled(source.local_user_id, stored["id"], False, None)

    asyncio.run(bootstrap_local_autonomous(source, repository=repo,
        client_factory=FakeClient, verify_openai=False))
    migrated = schedules.get_profile_schedule(source.local_user_id, first["profile_ref"])

    assert migrated["enabled"] is False and migrated["next_run_at"] is None
    assert migrated["expression"]["recurrence"]["interval_minutes"] == 15
