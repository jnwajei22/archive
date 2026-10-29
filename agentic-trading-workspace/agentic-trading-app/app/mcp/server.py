from pathlib import Path

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.mcp import tools
from app.models.tradelocker import TRADELOCKER_ACCOUNT_STATUS_OUTPUT_SCHEMA


mcp = FastMCP(
    "Agentic Forex Desk",
    instructions=(
        "This server retrieves market data and provides a presentation-only chart component. "
        "When a user requests a visible chart, first call get_market_candles, then calculate "
        "any requested overlays, then call render_market_chart using the returned series_id. "
        "Never claim that a chart is visible unless render_market_chart completed successfully. "
        "get_market_candles alone does not produce a visible chart, and render_market_chart does "
        "not retrieve data or calculate indicators. TradeLocker remains authoritative for "
        "execution prices; Finnhub and FRED are context sources. This server does not provide "
        "technical analysis, forecasts, or trade recommendations. For setup_required responses, give the setup_url "
        "to the user and ask them to connect with the same Auth0 account before retrying. "
        "When an authenticated user asks to start, stop, or emergency-stop autonomous trading, use the corresponding "
        "single ChatGPT-first autonomous control tool instead of asking for provider, model, schedule, arming, or mode settings."
    ),
)

mcp.tool(tools.get_forex_watchlist)
mcp.tool(tools.get_market_candles)
mcp.tool(
    tools.render_market_chart,
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"}, "series_id": {"type": "string"},
            "symbol": {"type": "string"}, "timeframe": {"type": "string"},
            "source": {"type": "string"}, "chart_type": {"type": "string"},
            "candles_rendered": {"type": "integer"},
            "horizontal_overlays": {"type": "integer"},
            "line_overlays": {"type": "integer"}, "markers": {"type": "integer"},
            "complete": {"type": "boolean"},
        },
        "required": ["status"],
    },
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    meta={
        "ui": {"resourceUri": "ui://widget/market-chart-v1.html"},
        "openai/outputTemplate": "ui://widget/market-chart-v1.html",
        "openai/toolInvocation/invoking": "Rendering market chart…",
        "openai/toolInvocation/invoked": "Market chart rendered.",
    },
)
mcp.tool(tools.get_watchlist_market_data)
mcp.tool(tools.get_economic_calendar)
mcp.tool(tools.get_market_news)
mcp.tool(tools.search_macro_series)
mcp.tool(tools.get_macro_series)
mcp.tool(tools.get_macro_release_calendar)
mcp.tool(tools.get_forex_research_bundle)
mcp.tool(tools.get_provider_capabilities)
mcp.tool(tools.review_forex_order)
mcp.tool(tools.set_kill_switch)
mcp.tool(tools.start_autonomous_trading,
    annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
mcp.tool(tools.stop_autonomous_trading,
    annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.emergency_stop_autonomous_trading,
    annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.get_autonomous_trading_status,
    annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(
    tools.get_autonomous_demo_status,
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
)
mcp.tool(
    tools.get_autonomous_demo_snapshot,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
mcp.tool(
    tools.review_autonomous_demo_order,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
mcp.tool(
    tools.submit_autonomous_demo_order,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
)
mcp.tool(
    tools.record_autonomous_no_trade,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
mcp.tool(
    tools.get_autonomous_run_result,
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
mcp.tool(
    tools.run_autonomous_demo_profile,
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True),
)
mcp.tool(tools.list_autonomous_schedules,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.get_autonomous_schedule_status,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.list_recent_autonomous_runs,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.get_autonomous_daily_summary,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.get_demo_execution_status,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=False,openWorldHint=True))
mcp.tool(tools.get_demo_trading_snapshot,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=False,openWorldHint=True))
mcp.tool(tools.review_demo_order,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=True))
mcp.tool(tools.submit_demo_order,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
mcp.tool(tools.get_demo_execution_result,annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False))
mcp.tool(tools.review_cancel_demo_order,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=True))
mcp.tool(tools.submit_cancel_demo_order,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
mcp.tool(tools.review_close_demo_position,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=True))
mcp.tool(tools.submit_close_demo_position,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
mcp.tool(
    tools.get_account_status,
    output_schema=TRADELOCKER_ACCOUNT_STATUS_OUTPUT_SCHEMA,
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
)
mcp.tool(
    tools.get_paper_account_status,
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    ),
)
mcp.tool(tools.get_open_positions)
mcp.tool(tools.get_pending_orders)
mcp.tool(tools.get_trade_history)
mcp.tool(tools.get_tradelocker_connection_status)
mcp.tool(tools.get_my_broker_connection_status)
mcp.tool(tools.get_my_tradelocker_accounts)
mcp.tool(tools.list_my_tradelocker_connections)
mcp.tool(tools.list_my_tradelocker_accounts)
mcp.tool(tools.list_execution_profiles)
mcp.tool(
    tools.get_my_tradelocker_account_status,
    output_schema=TRADELOCKER_ACCOUNT_STATUS_OUTPUT_SCHEMA,
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
    ),
)
mcp.tool(tools.get_my_tradelocker_symbols)
mcp.tool(tools.get_my_tradelocker_quote)
mcp.tool(tools.get_my_tradelocker_candles)
mcp.tool(tools.get_tradelocker_accounts)
mcp.tool(tools.get_tradelocker_config)
mcp.tool(tools.get_tradelocker_symbols)
mcp.tool(tools.get_tradelocker_quote)

CHART_RESOURCE_URI = "ui://widget/market-chart-v1.html"
WIDGET_ASSET = Path(__file__).resolve().parents[2] / "widget" / "dist" / "index.html"
if not WIDGET_ASSET.is_file():
    raise RuntimeError(
        f"Market chart widget is not built. Run 'npm --prefix widget ci' and "
        f"'npm --prefix widget run build' before starting the backend."
    )
WIDGET_HTML = WIDGET_ASSET.read_text(encoding="utf-8")


@mcp.resource(
    CHART_RESOURCE_URI,
    name="market-chart-v1",
    description="Presentation-only interactive OHLC market chart.",
    mime_type="text/html;profile=mcp-app",
    meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}},
)
def market_chart_resource() -> str:
    return WIDGET_HTML

mcp_app = mcp.http_app(path="/", stateless_http=True)
