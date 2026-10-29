from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.brokers.tradelocker.adapter import TradeLockerAdapter
from app.brokers.tradelocker.client import TradeLockerError
from app.config.settings import settings
from app.main import app
from app.mcp import tools
from app.mcp import auth
from app.mcp.server import mcp
from app.auth.identity import reset_current_claims, set_current_claims
from app.models.providers import MarketCandle, MarketSeries
from app.services.market_data.series_cache import market_series_cache


EXPECTED_TOOLS = {
    "get_forex_watchlist",
    "get_market_candles",
    "render_market_chart",
    "get_watchlist_market_data",
    "get_economic_calendar",
    "get_market_news",
    "search_macro_series",
    "get_macro_series",
    "get_macro_release_calendar",
    "get_forex_research_bundle",
    "get_provider_capabilities",
    "review_forex_order",
    "set_kill_switch",
    "start_autonomous_trading",
    "stop_autonomous_trading",
    "emergency_stop_autonomous_trading",
    "get_autonomous_trading_status",
    "get_account_status",
    "get_paper_account_status",
    "get_open_positions",
    "get_pending_orders",
    "get_trade_history",
    "get_tradelocker_connection_status",
    "get_my_broker_connection_status",
    "get_my_tradelocker_accounts",
    "get_my_tradelocker_account_status",
    "get_my_tradelocker_symbols",
    "get_my_tradelocker_quote",
    "get_my_tradelocker_candles",
    "get_tradelocker_accounts",
    "get_tradelocker_config",
    "get_tradelocker_symbols",
    "get_tradelocker_quote",
    "get_autonomous_demo_status",
    "get_autonomous_demo_snapshot",
    "review_autonomous_demo_order",
    "submit_autonomous_demo_order",
    "record_autonomous_no_trade",
    "get_autonomous_run_result",
    "run_autonomous_demo_profile",
    "list_autonomous_schedules",
    "get_autonomous_schedule_status",
    "list_recent_autonomous_runs",
    "get_autonomous_daily_summary",
}
EXPECTED_TOOLS.update({
    "list_my_tradelocker_connections",
    "list_my_tradelocker_accounts",
    "list_execution_profiles",
    "get_demo_execution_status","get_demo_trading_snapshot","review_demo_order","submit_demo_order",
    "get_demo_execution_result","review_cancel_demo_order","submit_cancel_demo_order",
    "review_close_demo_position","submit_close_demo_position",
})
INITIALIZE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1.0"},
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _enable_test_oauth(monkeypatch, scopes="forex:read forex:preview"):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(auth,"OAuthRepository",lambda:type("TestTokens",(),{
        "access_token_status":lambda self,token,resource:("accepted",{"sub":"auth0|test","scope":scopes,
            "aud":resource,"resource":resource})})())


def test_missing_auth_issuer_is_invalid_when_oauth_is_required(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(settings, "auth_issuer", None)
    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 503
    assert "AUTH_ISSUER is not configured" in response.json()["detail"]
    assert "authorization_servers" not in response.json()


def test_protected_resource_metadata_contains_oauth_configuration(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(settings, "auth_issuer", "https://tenant.auth0.com/")
    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "https://mcp.justinnwajei.com",
        "authorization_servers": ["https://mcp.justinnwajei.com"],
        "scopes_supported": ["forex:read", "forex:preview", "forex:execute"],
    }


def test_authorization_server_metadata_uses_agentic_forex_oauth_endpoints(monkeypatch):
    monkeypatch.setattr(settings, "auth_issuer", "https://tenant.auth0.com/")
    monkeypatch.setattr(settings, "auth_jwks_url", "https://tenant.auth0.com/.well-known/jwks.json")
    monkeypatch.setattr(settings, "oauth_authorization_url", "https://app.example.test/oauth/authorize")
    monkeypatch.setattr(settings, "oauth_token_url", None)
    with TestClient(app) as client:
        response = client.get("/.well-known/oauth-authorization-server")

    assert response.json()["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert response.json()["response_types_supported"] == ["code"]
    assert response.json()["code_challenge_methods_supported"] == ["S256"]

    assert response.status_code == 200
    metadata = response.json()
    assert metadata["authorization_endpoint"] == "https://app.example.test/oauth/authorize"
    assert metadata["token_endpoint"] == "https://mcp.justinnwajei.com/oauth/token"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["client_id_metadata_document_supported"] is True
    assert "registration_endpoint" not in metadata


def test_mcp_endpoint_initializes_and_advertises_tools(monkeypatch):
    _enable_test_oauth(monkeypatch)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/mcp",
            json=INITIALIZE_PAYLOAD,
            headers={**MCP_HEADERS, "Authorization": "Bearer valid-jwt"},
        )
        tool_response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={**MCP_HEADERS, "Authorization": "Bearer valid-jwt"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"tools":{"listChanged":true}' in response.text
    assert '"name":"Agentic Forex Desk"' in response.text
    assert tool_response.status_code == 200
    assert '"name":"get_tradelocker_accounts"' in tool_response.text
    for tool_name in EXPECTED_TOOLS:
        assert f'"name":"{tool_name}"' in tool_response.text


def test_mcp_missing_token_returns_oauth_challenge(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    with TestClient(app) as client:
        response = client.post("/mcp", json=INITIALIZE_PAYLOAD, headers=MCP_HEADERS)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://mcp.justinnwajei.com/'
        '.well-known/oauth-protected-resource"'
    )


def test_mcp_invalid_auth_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(
        auth,
        "_verify_access_token",
        lambda token: (_ for _ in ()).throw(auth.jwt.InvalidTokenError()),
    )
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=INITIALIZE_PAYLOAD,
            headers={**MCP_HEADERS, "Authorization": "Bearer wrong-secret"},
        )

    assert response.status_code == 401


def test_mcp_valid_jwt_is_accepted(monkeypatch):
    _enable_test_oauth(monkeypatch)
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=INITIALIZE_PAYLOAD,
            headers={**MCP_HEADERS, "Authorization": "Bearer valid-jwt"},
        )

    assert response.status_code == 200
    assert '"name":"Agentic Forex Desk"' in response.text


def test_mcp_oauth_rejections_are_diagnostic_and_do_not_log_tokens(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "oauth-diagnostics.db"))
    secret_token = "opaque-secret-token-that-must-not-be-logged"
    with caplog.at_level("WARNING", logger="app.mcp.auth"):
        with TestClient(app) as client:
            missing = client.post("/mcp", json=INITIALIZE_PAYLOAD, headers=MCP_HEADERS)
            malformed = client.post(
                "/mcp", json=INITIALIZE_PAYLOAD,
                headers={**MCP_HEADERS, "Authorization": "Basic credentials"},
            )
            unknown = client.post(
                "/mcp", json=INITIALIZE_PAYLOAD,
                headers={**MCP_HEADERS, "Authorization": f"Bearer {secret_token}"},
            )

    assert (missing.status_code, malformed.status_code, unknown.status_code) == (401, 401, 401)
    assert "reason=missing_authorization_header" in caplog.text
    assert "reason=malformed_bearer_header" in caplog.text
    assert "reason=unknown_token_fingerprint" in caplog.text
    assert "storage_reason=token_record_not_found" in caplog.text
    assert secret_token not in caplog.text


def test_jwt_verification_checks_signature_issuer_audience_and_expiry(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://tenant.auth0.com/"
    audience = "https://mcp.justinnwajei.com"
    monkeypatch.setattr(settings, "auth_issuer", issuer)
    monkeypatch.setattr(settings, "auth_audience", audience)
    monkeypatch.setattr(settings, "auth_jwks_url", "https://tenant.auth0.com/.well-known/jwks.json")
    monkeypatch.setitem(
        auth._jwks_clients,
        settings.auth_jwks_url,
        SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(
                key=private_key.public_key()
            )
        ),
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "exp": now + timedelta(minutes=5),
            "scope": "forex:read",
        },
        private_key,
        algorithm="RS256",
    )

    assert auth._verify_access_token(token)["scope"] == "forex:read"

    expired = jwt.encode(
        {"iss": issuer, "aud": audience, "exp": now - timedelta(minutes=1)},
        private_key,
        algorithm="RS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        auth._verify_access_token(expired)


def test_public_no_auth_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(settings, "mcp_allow_public_no_auth", False)
    with TestClient(app, base_url="https://public.example") as client:
        response = client.post("/mcp", json=INITIALIZE_PAYLOAD, headers=MCP_HEADERS)

    assert response.status_code == 401


def test_insufficient_scope_is_rejected(monkeypatch):
    _enable_test_oauth(monkeypatch, scopes="forex:read")
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "review_forex_order", "arguments": {}},
            },
            headers={**MCP_HEADERS, "Authorization": "Bearer read-only-jwt"},
        )

    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert 'scope="forex:preview"' in response.headers["www-authenticate"]


@pytest.mark.parametrize(("tool_name", "required_scope"), [
    ("get_demo_execution_status", "forex:read"),
    ("review_demo_order", "forex:preview"),
    ("submit_demo_order", "forex:execute"),
])
def test_profile_demo_tools_accept_only_their_required_scope(
    monkeypatch, tool_name, required_scope
):
    payload = {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": tool_name, "arguments": {}},
    }
    _enable_test_oauth(monkeypatch, scopes=required_scope)
    with TestClient(app) as client:
        accepted = client.post(
            "/mcp", json=payload,
            headers={**MCP_HEADERS, "Authorization": "Bearer scoped-token"},
        )
    assert accepted.status_code == 200

    _enable_test_oauth(monkeypatch, scopes="")
    with TestClient(app) as client:
        denied = client.post(
            "/mcp", json=payload,
            headers={**MCP_HEADERS, "Authorization": "Bearer unscoped-token"},
        )
    assert denied.status_code == 403
    assert f'scope="{required_scope}"' in denied.headers["www-authenticate"]


def test_read_scope_cannot_trigger_autonomous_execution(monkeypatch):
    _enable_test_oauth(monkeypatch,scopes="forex:read")
    with TestClient(app) as client:
        response=client.post("/mcp",json={"jsonrpc":"2.0","id":3,"method":"tools/call",
            "params":{"name":"run_autonomous_demo_profile","arguments":{"profile_ref":"p","run_key":"scheduled-key","trigger_reason":"test"}}},
            headers={**MCP_HEADERS,"Authorization":"Bearer read-only-jwt"})
    assert response.status_code==403
    assert 'scope="forex:execute"' in response.headers["www-authenticate"]


def test_read_scope_cannot_start_chatgpt_managed_autonomy(monkeypatch):
    _enable_test_oauth(monkeypatch,scopes="forex:read")
    with TestClient(app) as client:
        response=client.post("/mcp",json={"jsonrpc":"2.0","id":4,"method":"tools/call",
            "params":{"name":"start_autonomous_trading","arguments":{"environment":"demo"}}},
            headers={**MCP_HEADERS,"Authorization":"Bearer read-only-jwt"})
    assert response.status_code==403
    assert 'scope="forex:execute"' in response.headers["www-authenticate"]


def test_shared_secret_remains_available_for_explicit_manual_mode(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_oauth", False)
    monkeypatch.setattr(settings, "mcp_shared_secret", "test-mcp-secret")
    monkeypatch.setattr(settings, "mcp_allow_public_no_auth", False)
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json=INITIALIZE_PAYLOAD,
            headers={**MCP_HEADERS, "Authorization": "Bearer test-mcp-secret"},
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_mcp_server_registers_expected_tools():
    listed_tools = await mcp.list_tools()
    registered = {tool.name for tool in listed_tools}
    assert registered == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_every_registered_tool_has_an_explicit_oauth_scope():
    from app.mcp.auth import TOOL_SCOPES
    registered={tool.name for tool in await mcp.list_tools()}
    assert registered==set(TOOL_SCOPES)
    assert TOOL_SCOPES["run_autonomous_demo_profile"]=="forex:execute"
    assert TOOL_SCOPES["start_autonomous_trading"]=="forex:execute"
    assert TOOL_SCOPES["stop_autonomous_trading"]=="forex:execute"
    assert TOOL_SCOPES["emergency_stop_autonomous_trading"]=="forex:execute"
    assert TOOL_SCOPES["get_autonomous_trading_status"]=="forex:read"
    assert TOOL_SCOPES["review_demo_order"]=="forex:preview"
    assert TOOL_SCOPES["review_cancel_demo_order"]=="forex:preview"
    assert TOOL_SCOPES["review_close_demo_position"]=="forex:preview"
    assert TOOL_SCOPES["submit_demo_order"]=="forex:execute"
    assert TOOL_SCOPES["submit_cancel_demo_order"]=="forex:execute"
    assert TOOL_SCOPES["submit_close_demo_position"]=="forex:execute"
    assert "get_tradelocker_connection_status" in registered


@pytest.mark.asyncio
async def test_chatgpt_first_autonomy_tools_have_bounded_public_schemas():
    registered={tool.name:tool for tool in await mcp.list_tools()}
    start=registered["start_autonomous_trading"]
    assert set(start.parameters["properties"]) == {"environment","account_alias","profile_ref","run_now",
        "use_strategy_schedule","live_confirmation"}
    assert start.parameters["properties"]["environment"]["enum"] == ["demo","live"]
    assert registered["stop_autonomous_trading"].annotations.idempotentHint is True
    assert registered["get_autonomous_trading_status"].annotations.readOnlyHint is True
    for forbidden in ("decision_provider","model_identifier","openai_api_key","execution_mode","armed_until","shadow_mode"):
        assert forbidden not in start.parameters["properties"]


@pytest.mark.asyncio
async def test_demo_submission_schema_cannot_target_an_account_or_edit_order():
    submit = next(tool for tool in await mcp.list_tools() if tool.name == "submit_autonomous_demo_order")
    assert set(submit.parameters["properties"]) == {"preview_id", "idempotency_key"}
    assert auth.TOOL_SCOPES["submit_autonomous_demo_order"] == "forex:execute"
    assert submit.annotations.destructiveHint is True
    review = next(tool for tool in await mcp.list_tools() if tool.name == "review_autonomous_demo_order")
    assert set(review.parameters["properties"]) == {
        "snapshot_id", "pair", "side", "order_type", "entry", "stop_loss",
        "take_profit", "reason_codes", "profile_ref",
    }
    for name in ("submit_demo_order","submit_cancel_demo_order","submit_close_demo_position"):
        tool=next(item for item in await mcp.list_tools() if item.name==name)
        assert set(tool.parameters["properties"])=={"preview_id","idempotency_key"}
        assert auth.TOOL_SCOPES[name]=="forex:execute"
    for forbidden in ("accountId", "accNum", "environment", "base_url", "quantity"):
        assert forbidden not in submit.parameters["properties"]
        assert forbidden not in review.parameters["properties"]


@pytest.mark.asyncio
async def test_market_candle_tool_schema_scope_and_client_rendering_instructions():
    candle_tool = next(tool for tool in await mcp.list_tools() if tool.name == "get_market_candles")
    assert set(candle_tool.parameters["properties"]) == {
        "symbol", "timeframe", "source", "lookback", "start_time", "end_time", "max_candles",
        "account_alias", "account_ref",
    }
    assert auth.TOOL_SCOPES["get_market_candles"] == "forex:read"
    assert "client-side" in candle_tool.description
    assert "get_market_candles alone does not produce a visible chart" in mcp.instructions
    chart_tool = next(tool for tool in await mcp.list_tools() if tool.name == "render_market_chart")
    assert chart_tool.meta["ui"]["resourceUri"] == "ui://widget/market-chart-v1.html"
    assert chart_tool.meta["openai/outputTemplate"] == "ui://widget/market-chart-v1.html"
    assert chart_tool.annotations.readOnlyHint is True
    assert chart_tool.annotations.destructiveHint is False
    assert chart_tool.annotations.openWorldHint is False
    resources = await mcp.list_resources()
    resource = next(item for item in resources if str(item.uri) == "ui://widget/market-chart-v1.html")
    assert resource.mime_type == "text/html;profile=mcp-app"


@pytest.mark.asyncio
async def test_render_result_reaches_fastmcp_wire_meta_without_model_duplication():
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    series = MarketSeries(
        symbol="EURUSD", normalized_symbol="EURUSD", timeframe="1H", source="tradelocker",
        actual_start=now, actual_end=now, candles_returned=1, complete=True,
        retrieved_at=now,
        candles=[MarketCandle(timestamp=now, open=1.1, high=1.2, low=1.0, close=1.15)],
    )
    entry = market_series_cache.put("wire-user", series)
    token = set_current_claims({"sub": "wire-user"})
    try:
        result = await mcp.call_tool("render_market_chart", {"series_id": entry.series_id})
    finally:
        reset_current_claims(token)
        market_series_cache.clear()
    assert result.meta and len(result.meta["chart"]["candles"]) == 1
    assert result.structured_content["status"] == "ready"
    assert "candles" not in result.structured_content


@pytest.mark.asyncio
async def test_account_status_tool_is_labeled_read_only_and_not_paper():
    account_tool = next(tool for tool in await mcp.list_tools() if tool.name == "get_account_status")
    assert "normalized, labeled" in account_tool.description
    assert "never returns paper" in account_tool.description
    assert account_tool.annotations.readOnlyHint is True
    assert account_tool.annotations.destructiveHint is False
    assert "additionalProperties" not in account_tool.output_schema or (
        account_tool.output_schema["additionalProperties"] is not True
    )
    assert auth.TOOL_SCOPES["get_account_status"] == "forex:read"


@pytest.mark.asyncio
async def test_account_status_wire_response_is_canonical_top_level(monkeypatch):
    class Result:
        def model_dump(self, mode):
            assert mode == "json"
            return {
                "schema_version": "1.0", "status": "ok", "source": "tradelocker",
                "retrieved_at": "2026-07-15T22:00:00Z",
                "account": {
                    "account_id": "780896", "account_number": "2", "name": "HEROFX#2",
                    "currency": "USD", "environment": "demo", "active": True,
                },
                "balance": 0.0, "projected_balance": 0.0, "available_funds": 0.0,
                "blocked_balance": 0.0, "cash_balance": 0.0,
                "withdrawal_available": 0.0, "open_gross_pnl": 0.0,
                "open_net_pnl": 0.0, "positions_count": 0,
                "pending_orders_count": 0,
                "today": {"gross": 0.0, "net": 0.0, "fees": 0.0, "volume": 0.0, "trades_count": 0},
                "margin": {
                    "initial_requirement": 0.0, "maintenance_requirement": 0.0,
                    "warning_level": 100.0, "stop_out_level": 166.67,
                    "warning_requirement": 0.0, "margin_before_warning": 0.0,
                },
            }

    class Service:
        async def retrieve(self, user):
            assert user == "auth0|wire-user"
            return Result()

    monkeypatch.setattr(tools, "TradeLockerAccountStatusService", Service)
    token = set_current_claims({"sub": "auth0|wire-user"})
    try:
        result = await mcp.call_tool("get_account_status", {})
    finally:
        reset_current_claims(token)
    assert result.structured_content["balance"] == 0.0
    assert result.structured_content["margin"]["warning_level"] == 100.0
    assert "result" not in result.structured_content
    assert "accountDetailsData" not in str(result.structured_content)
    registered = {tool.name for tool in await mcp.list_tools()}
    assert not registered & {"generate_chart", "generate_static_forex_chart", "get_forex_chart_data"}


@pytest.mark.asyncio
async def test_mcp_account_discovery_tool_returns_client_result(monkeypatch):
    result = {"accounts": [{"accountId": 12345, "accNum": 2}]}
    client = SimpleNamespace(get_accounts=AsyncMock(return_value=result))
    monkeypatch.setattr(
        tools,
        "get_tradelocker_adapter",
        lambda: SimpleNamespace(client=client),
    )

    assert await tools.get_tradelocker_accounts() == result
    client.get_accounts.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_mcp_account_discovery_tool_rejects_missing_login_config(monkeypatch):
    client = SimpleNamespace(
        get_accounts=AsyncMock(
            side_effect=TradeLockerError(
                "get_accounts",
                "TradeLocker login configuration is incomplete: TRADELOCKER_PASSWORD.",
                code="not_configured",
            )
        )
    )
    monkeypatch.setattr(
        tools,
        "get_tradelocker_adapter",
        lambda: SimpleNamespace(client=client),
    )

    result = await tools.get_tradelocker_accounts()

    assert result["status"] == "error"
    assert result["error"] == "not_configured"
    assert "TRADELOCKER_PASSWORD" in result["message"]


@pytest.mark.asyncio
async def test_mcp_watchlist_is_configuration_only():
    watchlist = tools.get_forex_watchlist()
    assert watchlist
    assert watchlist[0]["pair"] == "EUR/USD"


def test_autonomous_kill_switch_does_not_block_manual_mcp_review(monkeypatch):
    submit_order = AsyncMock(side_effect=AssertionError("TradeLocker invoked"))
    monkeypatch.setattr(TradeLockerAdapter, "submit_order", submit_order)
    monkeypatch.setattr(settings, "kill_switch_enabled", True)

    preview = tools.review_forex_order(
        {
            "pair": "EUR/USD",
            "side": "long",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
            "risk_percent": 0.5,
        }
    )

    assert preview["status"] == "preview_only"
    assert "Kill switch is enabled." not in preview["violations"]
    submit_order.assert_not_awaited()


def test_remote_mcp_cannot_disable_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "kill_switch_enabled", True)
    monkeypatch.setattr(tools, "get_current_user_sub", lambda: "test-user")
    result = tools.set_kill_switch(False, "remote request")

    assert result["changed"] is False
    assert result["kill_switch_enabled"] is True
    assert settings.kill_switch_enabled is True
