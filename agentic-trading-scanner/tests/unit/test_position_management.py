from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.brokers.tradelocker.client import TradeLockerError
from app.config.settings import settings
from app.services.autonomous.decision import ManagementAction, PositionManagementDecision
from app.services.autonomous.execution import AutonomousExecutionError, InstrumentMetadata
from app.services.autonomous.position_management import (NoPositionManagementProvider,
    PositionManagementService, management_gate_reason)
from app.storage.execution import ExecutionRepository


def metadata() -> InstrumentMetadata:
    return InstrumentMetadata(instrument_id="1", route_id="2", contract_size=100000,
        pip_size=0.0001, lot_step=0.01, min_lots=0.01, max_lots=100,
        quote_currency="USD", minimum_stop_distance=0.0002,
        commission_per_lot=7, tick_size=0.00001, price_precision=5)


def decision(action: ManagementAction, **updates) -> PositionManagementDecision:
    values={"action":action,"position_id":"position-1","symbol":"EURUSD",
        "account_id":"account-1","confidence":0.95,"rationale":"Validated risk adjustment.",
        "source_timeframes":["15m"],
        "source_data_timestamps":[datetime.now(timezone.utc).isoformat()]}
    values.update(updates)
    return PositionManagementDecision(**values)


def normalize(value: PositionManagementDecision, row: dict | None=None, broker_metadata: InstrumentMetadata | None=None):
    service=object.__new__(PositionManagementService)
    position={"positionId":"position-1","symbol":"EURUSD","side":"buy","qty":0.53,
        "entryPrice":1.1000,"stopLoss":1.0950,"takeProfit":1.1200,**(row or {})}
    return service._normalize_position_action(value,position,1.1050,1.1052,broker_metadata or metadata())


def test_full_close_uses_current_position_and_does_not_require_entry_price():
    result=normalize(decision(ManagementAction.CLOSE),{"entryPrice":None})
    assert result["normalized_close_quantity"]==pytest.approx(0.53)
    assert result["remaining_quantity"]==0


def test_partial_reduce_rounds_close_quantity_and_preserves_valid_remainder():
    result=normalize(decision(ManagementAction.REDUCE,close_percent=30))
    assert result["normalized_close_quantity"]==pytest.approx(0.15)
    assert result["remaining_quantity"]==pytest.approx(0.38)


def test_partial_reduce_rejects_full_close_and_dust():
    with pytest.raises(AutonomousExecutionError) as full:
        normalize(decision(ManagementAction.REDUCE,close_percent=100))
    assert full.value.code=="partial_close_would_fully_close"
    with pytest.raises(AutonomousExecutionError) as dust:
        invalid_metadata=metadata()
        invalid_metadata=InstrumentMetadata(**{**invalid_metadata.__dict__,"min_lots":0.02})
        normalize(decision(ManagementAction.REDUCE,close_quantity=0.52),broker_metadata=invalid_metadata)
    assert dust.value.code=="dust_remainder"


def test_stop_can_tighten_but_never_loosen_for_long_or_short():
    tightened=normalize(decision(ManagementAction.MOVE_STOP,requested_stop_loss=1.1000))
    assert tightened["accepted_stop_loss"]==pytest.approx(1.1)
    with pytest.raises(AutonomousExecutionError) as long_error:
        normalize(decision(ManagementAction.MOVE_STOP,requested_stop_loss=1.0900))
    assert long_error.value.code=="stop_would_loosen"
    short={"side":"sell","stopLoss":1.1150,"entryPrice":1.1100}
    tightened_short=normalize(decision(ManagementAction.MOVE_STOP,requested_stop_loss=1.1080),short)
    assert tightened_short["accepted_stop_loss"]==pytest.approx(1.108)
    with pytest.raises(AutonomousExecutionError) as short_error:
        normalize(decision(ManagementAction.MOVE_STOP,requested_stop_loss=1.1200),short)
    assert short_error.value.code=="stop_would_loosen"


def test_break_even_includes_spread_buffer_commission_and_swap(monkeypatch):
    monkeypatch.setattr(settings,"autonomous_break_even_buffer_pips",1.0)
    result=normalize(decision(ManagementAction.BREAK_EVEN),{"swapAmount":-2.65})
    assert result["accepted_stop_loss"]>1.1003


def test_trailing_and_take_profit_are_deterministic_and_direction_checked():
    trail=normalize(decision(ManagementAction.TRAIL_STOP,trailing_distance=0.0010))
    assert trail["accepted_stop_loss"]==pytest.approx(1.104)
    target=normalize(decision(ManagementAction.MOVE_TAKE_PROFIT,requested_take_profit=1.115))
    assert target["accepted_take_profit"]==pytest.approx(1.115)
    with pytest.raises(AutonomousExecutionError) as error:
        normalize(decision(ManagementAction.MOVE_TAKE_PROFIT,requested_take_profit=1.100))
    assert error.value.code=="take_profit_distance_invalid"


def test_stale_or_missing_source_data_fails_closed():
    missing=decision(ManagementAction.CLOSE,source_data_timestamps=[])
    with pytest.raises(AutonomousExecutionError) as missing_error:
        PositionManagementService._validate_source_age(missing)
    assert missing_error.value.code=="stale_management_decision"
    old=decision(ManagementAction.CLOSE,source_data_timestamps=[
        (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()])
    with pytest.raises(AutonomousExecutionError) as old_error:
        PositionManagementService._validate_source_age(old)
    assert old_error.value.code=="stale_management_decision"


def test_management_gates_keep_emergency_risk_reduction_independent(monkeypatch):
    monkeypatch.setattr(settings,"live_trading_enabled",True)
    monkeypatch.setattr(settings,"autonomous_position_management_enabled",False)
    monkeypatch.setattr(settings,"autonomous_emergency_exit_enabled",True)
    assert management_gate_reason(ManagementAction.CLOSE)=="position_management_disabled"
    assert management_gate_reason(ManagementAction.CLOSE,emergency=True) is None
    monkeypatch.setattr(settings,"live_trading_enabled",False)
    assert management_gate_reason(ManagementAction.CLOSE,emergency=True)=="broker_writes_disabled"


def test_durable_action_key_and_position_lease_prevent_duplicates(tmp_path):
    repo=ExecutionRepository(tmp_path/"management.db")
    preview={"id":"preview-1","user_sub":"u","action_type":"close","profile_ref":"p",
        "account_record_id":"ar","connection_ref":"cr","connection_id":"c","account_id":"a",
        "acc_num":"7","account_alias":"demo","environment":"demo","server":"HeroFX",
        "base_url":"https://demo.tradelocker.test/backend-api","demo_classification":"demo",
        "target_id":"position-1","target_json":{},"status":"approved",
        "expires_at":datetime.now(timezone.utc).isoformat(),"created_at":datetime.now(timezone.utc).isoformat(),
        "action_key":"same-action"}
    repo.insert_action_preview(preview)
    claimed,_=repo.claim_action("exec-1","preview-1","u","key-1","close","position-1","same-action")
    assert claimed is True
    preview["id"]="preview-2";repo.insert_action_preview(preview)
    claimed,existing=repo.claim_action("exec-2","preview-2","u","key-2","close","position-1","same-action")
    assert claimed is False and existing["id"]=="exec-1"
    assert repo.acquire_management_lease("a:position-1","owner-1",60) is True
    assert repo.acquire_management_lease("a:position-1","owner-2",60) is False


class ManagementClient:
    def __init__(self):
        self.positions=[["position-1","EURUSD","buy",0.53,1.1,None,1.12]]
        self.close_calls=[]
    async def get_config(self):
        columns=lambda *names:{"columns":[{"id":name} for name in names]}
        return {"d":{"positionsConfig":columns("positionId","symbol","side","qty","entryPrice","stopLoss","takeProfit"),
            "ordersConfig":columns("orderId","symbol")}}
    async def get_open_positions(self):return {"d":{"positions":self.positions}}
    async def get_orders(self):return {"d":{"orders":[]}}
    async def get_quote(self,symbol):return {"bid":1.1050,"ask":1.1052}
    async def get_instrument_details(self,symbol):
        return {"instrument_id":"1","listing":{"routes":[{"type":"TRADE","id":"2"}]},
            "details":{"contractSize":100000,"pipSize":0.0001,"tickSize":0.00001,
                "lotStep":0.01,"minOrderQty":0.01,"minStopLossDistance":0.0002}}
    async def close_position(self,position_id,*,strategy_id):
        self.close_calls.append((position_id,strategy_id));self.positions=[];return {"accepted":True}
    async def aclose(self):pass


class ManagementDemo:
    def __init__(self,client):
        self.client=client
        self.context_value=SimpleNamespace(account_id="account-1",acc_num="7",profile_ref="profile-1",
            account_record_id="record-1",connection_ref="connection-1",connection_id="connection-db-1",
            account_alias="demo",environment="demo",server="HeroFX",
            base_url="https://demo.tradelocker.test/backend-api",demo_classification="demo",
            connection=object())
    async def context(self,user_sub,profile_ref,**kwargs):
        assert user_sub=="user" and profile_ref=="profile-1"
        return self.context_value
    def _client(self,connection,context):
        assert context.account_id=="account-1" and context.acc_num=="7"
        return self.client
    @staticmethod
    def _quote(payload):return float(payload["bid"]),float(payload["ask"])


@pytest.mark.asyncio
async def test_management_cycle_emergency_closes_exact_selected_position_before_entries(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"live_trading_enabled",True)
    monkeypatch.setattr(settings,"autonomous_emergency_exit_enabled",True)
    monkeypatch.setattr(settings,"autonomous_require_stop_loss",True)
    client=ManagementClient();repository=ExecutionRepository(tmp_path/"cycle.db")
    service=PositionManagementService(demo=ManagementDemo(client),execution=repository,
        provider=NoPositionManagementProvider())
    result=await service.run_cycle("user","profile-1","cycle-1")
    assert result["actions"][0]["status"]=="verified"
    assert result["skip_new_entries"] is True
    assert client.close_calls and client.close_calls[0][0]=="position-1"
    stored=repository.management_status("user")
    assert stored["latest_cycle"]["state"]=="completed"
    assert stored["unresolved_submissions"]==[]


@pytest.mark.asyncio
async def test_review_rejects_account_and_position_symbol_mismatch(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"live_trading_enabled",True)
    client=ManagementClient();service=PositionManagementService(demo=ManagementDemo(client),
        execution=ExecutionRepository(tmp_path/"mismatch.db"),provider=NoPositionManagementProvider())
    with pytest.raises(AutonomousExecutionError) as account_error:
        await service.review("user","profile-1",decision(ManagementAction.CLOSE,account_id="other"),"a"*64)
    assert account_error.value.code=="management_account_mismatch"
    with pytest.raises(AutonomousExecutionError) as symbol_error:
        await service.review("user","profile-1",decision(ManagementAction.CLOSE,symbol="GBPUSD"),"b"*64)
    assert symbol_error.value.code=="position_symbol_mismatch"
    assert client.close_calls==[]


class AmbiguousCloseClient(ManagementClient):
    async def close_position(self,position_id,*,strategy_id):
        self.close_calls.append((position_id,strategy_id))
        raise TradeLockerError("close_position","sanitized timeout",code="timeout")


@pytest.mark.asyncio
async def test_ambiguous_close_is_not_resubmitted_and_reconciles_after_restart(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,"live_trading_enabled",True)
    repository=ExecutionRepository(tmp_path/"restart.db");client=AmbiguousCloseClient()
    service=PositionManagementService(demo=ManagementDemo(client),execution=repository,
        provider=NoPositionManagementProvider())
    preview=await service.review("user","profile-1",decision(ManagementAction.CLOSE),"c"*64,emergency=True)
    first=await service.submit("user",preview["preview_id"],"management:stable",emergency=True)
    second=await service.submit("user",preview["preview_id"],"management:stable",emergency=True)
    assert first["status"]==second["status"]=="unknown"
    assert first["execution_id"]==second["execution_id"] and len(client.close_calls)==1
    assert len(repository.unresolved_management_actions("user"))==1

    # Simulate broker completion while this process was down. The restarted
    # service performs a read-only reconciliation and never dispatches again.
    client.positions=[]
    restarted=PositionManagementService(demo=ManagementDemo(client),
        execution=ExecutionRepository(tmp_path/"restart.db"),provider=NoPositionManagementProvider())
    result=await restarted.run_cycle("user","profile-1","restart-cycle")
    assert result["reconciled"][0]["verified"] is True
    assert repository.unresolved_management_actions("user")==[]
    assert len(client.close_calls)==1
