# Folder Map

```text
agentic-forex-desk/
  app/
    api/routes/              FastAPI HTTP routes
    mcp/                     MCP-exposed tools
    config/                  settings and environment config
    db/                      SQL schema and migrations
    models/                  Pydantic request/response/domain models
    services/
      market_data/           canonical candle retrieval and response contracts
      providers/             Finnhub/FRED clients, errors, and public-data cache
      risk/                  hard risk checks and position sizing
      trading/               previews, confirmation, trade flow
      logging/               audit and risk logs
    brokers/
      base.py                adapter interface
      paper/                 paper trading adapter
      tradelocker/           per-user broker/account market-data adapter
    webhooks/                optional untrusted TradingView alert receiver
  tests/
    unit/
    integration/
    fixtures/
  storage/                   database and non-chart application state
```
