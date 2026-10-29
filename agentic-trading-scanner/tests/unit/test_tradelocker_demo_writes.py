from __future__ import annotations

import pytest

from app.brokers.tradelocker.client import TradeLockerClient


@pytest.mark.asyncio
async def test_cancel_and_close_use_documented_scoped_routes():
    client=object.__new__(TradeLockerClient);client.account_number="7"
    calls=[]
    async def request(method,path,**kwargs):calls.append((method,path,kwargs));return {"ok":True}
    client._request=request
    await client.cancel_order("order-9")
    await client.close_position("position-4",strategy_id="afd-test")
    assert calls[0][0:2]==("DELETE","/trade/orders/order-9")
    assert calls[0][2]["headers"]["accNum"]=="7"
    assert calls[1][0:2]==("DELETE","/trade/positions/position-4")
    assert calls[1][2]["json"]=={"qty":0}
    assert calls[1][2]["params"]=={"strategyId":"afd-test"}


@pytest.mark.asyncio
async def test_partial_close_and_protection_use_documented_payloads():
    client=object.__new__(TradeLockerClient);client.account_number="7"
    calls=[]
    async def request(method,path,**kwargs):calls.append((method,path,kwargs));return {"ok":True}
    client._request=request
    await client.reduce_position("position-4",close_quantity=0.25,strategy_id="afd-test")
    await client.modify_position_protection("position-4",stop_loss=1.101,
        take_profit=1.12,strategy_id="afd-test")
    assert calls[0][0:2]==("DELETE","/trade/positions/position-4")
    assert calls[0][2]["json"]=={"qty":0.25}
    assert calls[1][0:2]==("PATCH","/trade/positions/position-4")
    assert calls[1][2]["json"]=={"stopLoss":1.101,"takeProfit":1.12}
    assert calls[1][2]["params"]=={"strategyId":"afd-test"}
