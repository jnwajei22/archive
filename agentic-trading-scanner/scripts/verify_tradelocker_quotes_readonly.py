"""Read-only quote and instrument-route verification for one demo account."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from typing import Any

from app.brokers.tradelocker.client import TradeLockerClient, TradeLockerError
from app.config.settings import settings
from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError
from app.storage.brokers import BrokerRepository


def _owner_for_alias(alias: str) -> str | None:
    with sqlite3.connect(settings.sqlite_path) as db:
        row = db.execute(
            """SELECT u.auth0_sub FROM broker_accounts a
               JOIN users u ON u.id=a.user_id WHERE a.account_alias=?""",
            (alias,),
        ).fetchone()
    return str(row[0]) if row else None


async def verify(alias: str, symbols: list[str]) -> dict[str, Any]:
    user_sub = _owner_for_alias(alias)
    if user_sub is None:
        return {"status": "blocked", "error": "account_alias_not_found", "alias": alias}
    repository = BrokerRepository()
    account = repository.get_account_record(user_sub, alias=alias)
    if account is None or account["environment"] != "demo" or account["is_demo"] != 1:
        return {"status": "blocked", "error": "verified_demo_account_required", "alias": alias}
    connection = repository.get_connection(user_sub, str(account["connection_ref"]))
    if connection is None:
        return {"status": "blocked", "error": "connection_unavailable", "alias": alias}
    client = TradeLockerClient(
        base_url=connection.base_url,
        username=connection.username,
        password=connection.password,
        server=connection.server,
        account_id=str(account["broker_account_id"]),
        account_number=str(account["acc_num"]),
        cache_user_id=user_sub,
        cache_connection_id=str(account["connection_ref"]),
        cache_account_record_id=str(account["public_id"]),
    )
    service = AutonomousDemoService()
    results: dict[str, Any] = {}
    try:
        for symbol in symbols:
            normalized = symbol.replace("/", "").upper()
            try:
                quote = await client.get_quote(normalized)
                bid, ask = service._quote(quote)
                details = await client.get_instrument_details(normalized)
                results[normalized] = {
                    "status": "ok",
                    "bid": bid,
                    "ask": ask,
                    "spread": ask - bid,
                    "instrument_id": str(details.get("instrument_id")),
                    "trade_route_id": str(details.get("trade_route_id")),
                }
            except TradeLockerError as exc:
                results[normalized] = {
                    "status": "blocked",
                    "stage": exc.operation,
                    "error": exc.code,
                    "http_status": exc.status_code,
                    "retry_after_seconds": exc.details.get("retry_after_seconds"),
                }
            except AutonomousExecutionError as exc:
                results[normalized] = {
                    "status": "blocked",
                    "stage": "quote_parse",
                    "error": exc.code,
                    "http_status": None,
                }
    finally:
        await client.aclose()
    return {"status": "ok", "account_alias": alias, "symbols": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="herofx-demo-1")
    parser.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD")
    args = parser.parse_args()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    print(json.dumps(asyncio.run(verify(args.alias, symbols)), indent=2))


if __name__ == "__main__":
    main()
