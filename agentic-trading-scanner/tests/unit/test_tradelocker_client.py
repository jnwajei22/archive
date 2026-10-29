from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest

from app.brokers.tradelocker.adapter import TradeLockerAdapter
from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError


def _token() -> str:
    return jwt.encode(
        {"exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        "test-only-key-that-is-at-least-32-bytes-long",
        algorithm="HS256",
    )


def _client(handler, **overrides) -> TradeLockerClient:
    config = {
        "base_url": "https://demo.tradelocker.test/backend-api",
        "username": "user@example.test",
        "password": "never-log-this",
        "server": "DEMO",
        "account_id": "12345",
        "account_number": "2",
        "transport": httpx.MockTransport(handler),
    }
    config.update(overrides)
    return TradeLockerClient(
        **config,
    )


@pytest.mark.asyncio
async def test_history_request_budget_stops_before_an_extra_broker_call():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"d": []})

    client = _client(handler)
    client._access_token = _token()
    client._token_expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()
    client.history_request_budget = 1
    try:
        await client._history_page(
            instrument_id="1", route_id="2", resolution="1H",
            start_time_ms=1, end_time_ms=2,
        )
        with pytest.raises(TradeLockerError) as error:
            await client._history_page(
                instrument_id="1", route_id="2", resolution="1H",
                start_time_ms=3, end_time_ms=4,
            )
        assert error.value.code == "history_request_budget_exhausted"
        assert calls == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_login_is_cached_and_account_reads_are_authenticated():
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            calls["login"] += 1
            return httpx.Response(201, json={"accessToken": _token()})
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.headers["accnum"] == "2"
        if request.url.path.endswith("/state"):
            return httpx.Response(200, json={"balance": 1000.0})
        if request.url.path.endswith("/positions"):
            return httpx.Response(200, json={"d": [[1, "EURUSD"]]})
        if request.url.path.endswith("/orders"):
            return httpx.Response(200, json={"d": []})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler) as client:
        assert await client.get_account_state_payload() == {"balance": 1000.0}
        assert await client.get_open_positions() == {"d": [[1, "EURUSD"]]}
        assert await client.get_orders() == {"d": []}

    assert calls["login"] == 1


@pytest.mark.asyncio
async def test_unauthorized_account_read_refreshes_token_once_without_changing_account():
    calls = {"login": 0, "state": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            calls["login"] += 1
            return httpx.Response(201, json={"accessToken": _token()})
        if request.url.path.endswith("/state"):
            calls["state"] += 1
            if calls["state"] == 1:
                return httpx.Response(401, json={"error": "expired"})
            assert request.headers["accnum"] == "2"
            return httpx.Response(200, json={"d": {"accountDetailsData": []}})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler) as client:
        assert await client.get_account_state_payload() == {"d": {"accountDetailsData": []}}
        assert client.account_id == "12345" and client.account_number == "2"
        assert client.token_refresh_count == 1
    assert calls == {"login": 2, "state": 2}


@pytest.mark.asyncio
async def test_config_accounts_and_symbols_use_documented_read_routes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        if request.url.path.endswith("/trade/config"):
            return httpx.Response(200, json={"positionsConfig": []})
        if request.url.path.endswith("/auth/jwt/all-accounts"):
            return httpx.Response(200, json={"accounts": [{"id": 12345, "accNum": 2}]})
        if request.url.path.endswith("/instruments"):
            return httpx.Response(200, json={"instruments": []})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler) as client:
        assert "positionsConfig" in await client.get_config()
        assert await client.get_accounts() == {
            "accounts": [{"accountId": 12345, "accNum": 2}]
        }
        assert await client.get_symbols() == {"instruments": []}


@pytest.mark.asyncio
async def test_place_order_uses_account_route_header_and_atomic_protections():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"orderId": "order-1"})

    order = {
        "qty": 0.01, "routeId": "trade-route", "side": "buy", "validity": "IOC",
        "type": "market", "tradableInstrumentId": 42, "price": 0,
        "stopLoss": 1.09, "stopLossType": "absolute",
        "takeProfit": 1.12, "takeProfitType": "absolute", "strategyId": "afd-test",
    }
    async with _client(handler) as client:
        assert await client.place_order(order) == {"orderId": "order-1"}
    assert captured["path"].endswith("/trade/accounts/12345/orders")
    assert captured["headers"]["accnum"] == "2"
    assert captured["body"]["stopLossType"] == "absolute"
    assert captured["body"]["takeProfitType"] == "absolute"


@pytest.mark.asyncio
async def test_place_order_rejects_caller_account_override_before_network():
    client = _client(lambda request: (_ for _ in ()).throw(AssertionError("network called")))
    with pytest.raises(TradeLockerError) as error:
        await client.place_order({"accountId": "other"})
    await client.aclose()
    assert error.value.code == "invalid_order"


@pytest.mark.asyncio
async def test_account_discovery_does_not_require_account_selection():
    secret_token = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": secret_token})
        assert request.url.path.endswith("/auth/jwt/all-accounts")
        assert "accnum" not in request.headers
        return httpx.Response(
            200,
            json={
                "accounts": [{
                    "id": 45678,
                    "accNum": 3,
                    "name": "Demo account",
                    "currency": "USD",
                    "accessToken": secret_token,
                    "password": "must-not-escape",
                }]
            },
        )

    async with _client(handler, account_id=None, account_number=None) as client:
        result = await client.get_accounts()

    assert result == {
        "accounts": [{
            "accountId": 45678,
            "accNum": 3,
            "name": "Demo account",
            "currency": "USD",
        }]
    }
    assert secret_token not in str(result)
    assert "must-not-escape" not in str(result)


@pytest.mark.asyncio
async def test_account_specific_config_requires_discovered_account_number():
    client = _client(lambda request: httpx.Response(500), account_number=None)

    with pytest.raises(TradeLockerError) as caught:
        await client.get_config()
    await client.aclose()

    assert caught.value.code == "not_configured"
    assert str(caught.value) == (
        "Account number is required for account-specific config. "
        "Run get_tradelocker_accounts first."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["password", "server"])
async def test_discovery_rejects_incomplete_login_config(missing):
    client = _client(lambda request: httpx.Response(500), **{missing: None}, account_number=None)

    with pytest.raises(TradeLockerError) as caught:
        await client.get_accounts()
    await client.aclose()

    expected_name = f"TRADELOCKER_{missing.upper()}"
    assert caught.value.code == "not_configured"
    assert expected_name in str(caught.value)
    assert "never-log-this" not in str(caught.value)


@pytest.mark.asyncio
async def test_discovery_does_not_log_password_or_token(caplog):
    secret_token = _token()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": secret_token})
        return httpx.Response(500, json={"accessToken": secret_token})

    async with _client(handler, account_id=None, account_number=None) as client:
        with pytest.raises(TradeLockerError):
            await client.get_accounts()

    assert "never-log-this" not in caplog.text
    assert secret_token not in caplog.text


@pytest.mark.asyncio
async def test_quote_and_candles_resolve_documented_info_route():
    candle_timestamp = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        if request.url.path.endswith("/instruments"):
            return httpx.Response(
                200,
                json={
                    "d": {"instruments": [{
                        "name": "EURUSD",
                        "tradableInstrumentId": 77,
                        "routes": [{"type": "INFO", "id": 9}],
                    }]}
                },
            )
        assert request.url.params["tradableInstrumentId"] == "77"
        assert request.url.params["routeId"] == "9"
        if request.url.path.endswith("/quotes"):
            return httpx.Response(200, json={"ask": 1.101, "bid": 1.1})
        if request.url.path.endswith("/history"):
            assert request.url.params["resolution"] == "1H"
            return httpx.Response(
                200, json={"t": [candle_timestamp], "o": [1.0], "h": [1.1], "l": [0.9], "c": [1.05]}
            )
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler) as client:
        assert (await client.get_quote("EUR/USD"))["ask"] == 1.101
        result = await client.get_candles("EUR/USD", "1h", 100)
        assert result.candles[0].close == 1.05
        assert result.batches_requested == 2


@pytest.mark.asyncio
async def test_unavailable_optional_endpoint_returns_not_implemented():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        return httpx.Response(404, json={"message": "missing"})

    async with _client(handler) as client:
        result = await client.get_orders()

    assert result["status"] == "not_implemented"
    assert result["operation"] == "get_orders"


@pytest.mark.asyncio
async def test_errors_are_sanitized_and_structured():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad credentials"})

    async with _client(handler) as client:
        with pytest.raises(TradeLockerError) as caught:
            await client.login()

    error = caught.value.as_dict()
    assert error["status_code"] == 401
    assert "user@example.test" not in str(error)
    assert "never-log-this" not in str(error)


@pytest.mark.asyncio
async def test_submit_order_remains_unimplemented():
    adapter = TradeLockerAdapter(client=_client(lambda request: httpx.Response(500)))
    with pytest.raises(NotImplementedError, match="intentionally disabled"):
        await adapter.submit_order(None)
    await adapter.client.aclose()


@pytest.mark.asyncio
async def test_direct_daily_candles_use_documented_resolution_and_map_abbreviations():
    end = int(datetime(2026, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)
    day = 86_400_000
    seen = []
    client = _client(lambda request: httpx.Response(500))

    async def resolve(symbol):
        assert symbol == "EURUSD"
        return 77, 9

    async def history(**kwargs):
        seen.append(kwargs)
        assert kwargs["instrument_id"] == 77 and kwargs["route_id"] == 9
        assert kwargs["resolution"] == "1D"
        timestamps = [end - day * 2, end - day]
        return {"t": timestamps, "o": [1.0, 2.0], "h": [1.2, 2.2],
                "l": [.9, 1.9], "c": [1.1, 2.1], "v": [0, 5]}

    client._resolve_instrument = resolve
    client._history_page = history
    result = await client.get_candles("EURUSD", "day", 2, end_time_ms=end)
    await client.aclose()

    assert result.complete and result.source == "direct"
    assert result.provider_timeframe_sent == "1D"
    assert [row.close for row in result.candles] == [1.1, 2.1]
    assert [row.volume for row in result.candles] == [0, 5]
    assert seen


@pytest.mark.asyncio
async def test_daily_fallback_aggregates_only_same_account_symbol_and_info_route():
    hour, day = 3_600_000, 86_400_000
    end = int(datetime(2026, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)
    first_day = end - (end % day) - day * 2
    hourly_rows = [
        {"t": first_day + hour * index, "o": 1 + index, "h": 2 + index,
         "l": .5 + index, "c": 1.5 + index, "v": 1}
        for index in range(48)
    ]
    requests = []
    client = _client(lambda request: httpx.Response(500), account_id="account-a", account_number="7")

    async def resolve(symbol):
        assert symbol == "EURUSD"
        return 77, 9

    async def history(**kwargs):
        requests.append((client.account_id, client.account_number, kwargs["instrument_id"],
                         kwargs["route_id"], kwargs["resolution"]))
        assert kwargs["instrument_id"] == 77 and kwargs["route_id"] == 9
        if kwargs["resolution"] == "1D":
            return []
        return [row for row in hourly_rows
                if kwargs["start_time_ms"] <= row["t"] <= kwargs["end_time_ms"]]

    client._resolve_instrument = resolve
    client._history_page = history
    result = await client.get_candles("EURUSD", "1d", 2, end_time_ms=end)
    await client.aclose()

    assert result.complete and result.source == "aggregated_1H"
    assert len(result.candles) == 2
    assert result.candles[0].open == 1 and result.candles[0].close == 24.5
    assert result.candles[0].volume == 24
    assert all(row[:4] == ("account-a", "7", 77, 9) for row in requests)
    assert {row[4] for row in requests} == {"1D", "1H"}


@pytest.mark.asyncio
async def test_incomplete_hourly_fallback_blocks_with_exact_diagnostics():
    hour, day = 3_600_000, 86_400_000
    end = int(datetime(2026, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)
    first_day = end - (end % day) - day * 2
    hourly_rows = [{"t": first_day + hour * index, "o": 1, "h": 2,
                    "l": .5, "c": 1.5, "v": 0} for index in range(47)]
    client = _client(lambda request: httpx.Response(500))

    async def resolve(symbol): return 77, 9
    async def history(**kwargs):
        if kwargs["resolution"] == "1D":
            return []
        return [row for row in hourly_rows
                if kwargs["start_time_ms"] <= row["t"] <= kwargs["end_time_ms"]]

    client._resolve_instrument = resolve
    client._history_page = history
    result = await client.get_candles("EURUSD", "1440", 2, end_time_ms=end)
    await client.aclose()

    assert not result.complete
    diagnostics = result.diagnostics()
    assert diagnostics["requested_timeframe"] == "1d"
    assert diagnostics["provider_timeframe_sent"] == "1D"
    assert diagnostics["http_status"] == 200
    assert diagnostics["broker_error_category"] == "incomplete_history"
    assert diagnostics["rows_received"] == 0
    assert diagnostics["mapping_failure"] is None
    assert diagnostics["fallback"]["provider_timeframe_sent"] == "1H"
    assert diagnostics["fallback"]["complete_daily_rows"] == 1


@pytest.mark.asyncio
async def test_daily_candles_cannot_cross_account_or_symbol_context():
    end = int(datetime(2026, 7, 18, tzinfo=timezone.utc).timestamp() * 1000)

    async def retrieve(account_id, account_number, symbol, instrument_id, close):
        client = _client(lambda request: httpx.Response(500),
                         account_id=account_id, account_number=account_number)

        async def resolve(requested_symbol):
            assert requested_symbol == symbol
            return instrument_id, 9

        async def history(**kwargs):
            assert client.account_id == account_id and client.account_number == account_number
            assert kwargs["instrument_id"] == instrument_id
            return [{"t": end - 86_400_000, "o": close, "h": close, "l": close,
                     "c": close, "v": 0}]

        client._resolve_instrument = resolve
        client._history_page = history
        result = await client.get_candles(symbol, "1D", 1, end_time_ms=end)
        await client.aclose()
        return result.candles[0]

    eurusd = await retrieve("account-a", "7", "EURUSD", 77, 1.1)
    gbpusd = await retrieve("account-b", "8", "GBPUSD", 88, 1.3)
    assert eurusd.close == 1.1
    assert gbpusd.close == 1.3


@pytest.mark.asyncio
@pytest.mark.parametrize(("timeframe", "provider", "duration"), [
    ("4h", "4H", 14_400_000), ("1h", "1H", 3_600_000), ("15m", "15m", 900_000),
])
async def test_strategy_timeframes_use_direct_normalized_history(timeframe, provider, duration):
    start = int(datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [{"t": start + duration * index, "o": "0", "h": "2",
             "l": "0", "c": "1.5", "v": "0"} for index in range(50)]
    end = rows[-1]["t"] + duration
    client = _client(lambda request: httpx.Response(500))

    async def resolve(symbol):
        assert symbol == "EURUSD"
        return 77, 9

    async def history(**kwargs):
        assert kwargs["resolution"] == provider
        return [row for row in rows
                if kwargs["start_time_ms"] <= row["t"] <= kwargs["end_time_ms"]]

    client._resolve_instrument = resolve
    client._history_page = history
    result = await client.get_candles(
        "EURUSD", timeframe, 50, end_time_ms=end, minimum_usable=50
    )
    await client.aclose()

    assert result.complete and result.source == "direct"
    assert result.provider_timeframe_sent == provider
    assert result.usable_count == 50
    assert result.candles[0].open == result.candles[0].low == result.candles[0].volume == 0
    canonical = result.canonical_dict()
    assert canonical["metadata"]["is_sufficient"] is True
    assert canonical["blocking_reasons"] == []


@pytest.mark.asyncio
async def test_unsupported_timeframe_returns_canonical_diagnostics_without_network():
    client = _client(lambda request: (_ for _ in ()).throw(AssertionError("network called")))
    with pytest.raises(TradeLockerError) as error:
        await client.get_candles("EURUSD", "2h", 50)
    await client.aclose()
    assert error.value.code == "unsupported_timeframe"
    assert error.value.details["requested_timeframe"] == "2h"
    assert "4h" in error.value.details["supported_internal_values"]
    assert error.value.details["provider_value_attempted"] is None
    assert error.value.details["error_category"] == "unsupported_timeframe"


class _ImmediateLimiter:
    async def acquire(self, connection_id):
        return type("Lease", (), {"connection_id": connection_id, "owner": "test"})()

    def release(self, lease):
        return None

    def set_cooldown(self, connection_id, seconds):
        return 0.0


@pytest.mark.asyncio
async def test_quote_resolution_reuses_one_instrument_catalog_for_multiple_symbols():
    calls = {"instruments": 0, "quotes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        if request.url.path.endswith("/instruments"):
            calls["instruments"] += 1
            return httpx.Response(200, json={"d": {"instruments": [
                {"name": "EURUSD", "tradableInstrumentId": 77,
                 "routes": [{"type": "INFO", "id": 9}, {"type": "TRADE", "id": 19}]},
                {"name": "GBPUSD", "tradableInstrumentId": 88,
                 "routes": [{"type": "INFO", "id": 10}, {"type": "TRADE", "id": 20}]},
            ]}})
        if request.url.path.endswith("/quotes"):
            calls["quotes"].append((
                request.url.params["tradableInstrumentId"],
                request.url.params["routeId"],
            ))
            return httpx.Response(200, json={"bid": 1.1, "ask": 1.2})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler, request_limiter=_ImmediateLimiter()) as client:
        await client.get_quote("EURUSD")
        await client.get_quote("GBPUSD")

    assert calls["instruments"] == 1
    assert calls["quotes"] == [("77", "9"), ("88", "10")]


@pytest.mark.asyncio
async def test_instrument_details_use_documented_trade_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/jwt/token"):
            return httpx.Response(201, json={"accessToken": _token()})
        if request.url.path.endswith("/instruments"):
            return httpx.Response(200, json={"d": {"instruments": [{
                "name": "EURUSD", "tradableInstrumentId": 77,
                "routes": [{"type": "INFO", "id": 9}, {"type": "TRADE", "id": 19}],
            }]}})
        if request.url.path.endswith("/trade/instruments/77"):
            seen["route_id"] = request.url.params["routeId"]
            return httpx.Response(200, json={"contractSize": 100000})
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with _client(handler, request_limiter=_ImmediateLimiter()) as client:
        result = await client.get_instrument_details("EURUSD")

    assert seen["route_id"] == "19"
    assert result["trade_route_id"] == 19
