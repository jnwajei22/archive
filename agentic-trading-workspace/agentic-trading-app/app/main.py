import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from app.api.routes.health import router as health_router
from app.api.routes.forex import router as forex_router
from app.api.routes.platform import router as platform_router
from app.api.routes.oauth import router as oauth_router
from app.api.routes.integrations import router as integrations_router
from app.webhooks.tradingview import router as tradingview_router
from app.mcp.server import mcp_app
from app.mcp.auth import MCPAuthMiddleware
from app.config.settings import settings
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.storage.brokers import BrokerStorageError
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository
from app.storage.runtime_settings import RuntimeSettingsRepository
from app.storage.signal_intents import SignalIntentRepository
from app.jobs.autonomous_scheduler import AutonomousSchedulerWorker
from app.oauth.constants import CANONICAL_MCP_RESOURCE


@asynccontextmanager
async def lifespan(app_instance):
    # Ordered, idempotent additive migrations run before either API or embedded worker accepts traffic.
    BrokerRepository();ExecutionRepository();ScheduleRepository();RuntimeSettingsRepository();SignalIntentRepository()
    worker=AutonomousSchedulerWorker() if settings.autonomous_scheduler_embedded else None
    task=None
    async with mcp_app.lifespan(app_instance):
        if worker:task=asyncio.create_task(worker.run_forever(),name="autonomous-scheduler")
        try:yield
        finally:
            if worker:
                worker.stop()
                if task:await task

app = FastAPI(
    title="Agentic Forex Desk",
    description="Focused forex research and execution-data backend for ChatGPT.",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(MCPAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(BrokerStorageError)
async def broker_storage_error_handler(request, exc: BrokerStorageError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_metadata() -> dict[str, object]:
    if settings.mcp_require_oauth and not settings.auth_issuer:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth is required but AUTH_ISSUER is not configured. "
                "Set AUTH_ISSUER to the issuer URL of a real OIDC/OAuth provider."
            ),
        )
    return {
        "resource": CANONICAL_MCP_RESOURCE,
        "authorization_servers": [CANONICAL_MCP_RESOURCE],
        "scopes_supported": ["forex:read", "forex:preview", "forex:execute"],
    }


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/openid-configuration")
async def oauth_authorization_server_metadata() -> dict[str, object]:
    if not settings.oauth_transaction_secret and not settings.broker_secret_key:
        raise HTTPException(
            status_code=503,
            detail="OAuth onboarding authorization endpoints are not configured.",
        )
    return {
        "issuer": CANONICAL_MCP_RESOURCE,
        "authorization_endpoint": settings.oauth_authorization_url
        or f"{CANONICAL_MCP_RESOURCE}/oauth/authorize",
        "token_endpoint": settings.oauth_token_url
        or f"{CANONICAL_MCP_RESOURCE}/oauth/token",
        "client_id_metadata_document_supported": True,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "profile", "email", "forex:read", "forex:preview", "forex:execute"],
        "token_endpoint_auth_methods_supported": ["none"],
    }

app.include_router(health_router)
app.include_router(forex_router)
app.include_router(platform_router)
app.include_router(oauth_router)
app.include_router(integrations_router)
app.include_router(tradingview_router, prefix="/webhooks", tags=["webhooks"])
app.mount("/mcp", mcp_app)
