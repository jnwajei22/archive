import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.platform import current_claims
from app.config.settings import settings
from app.oauth.constants import (
    CANONICAL_MCP_ENDPOINT,
    CANONICAL_MCP_RESOURCE,
    MCP_PROTECTED_RESOURCE_METADATA,
)
from app.storage.oauth import OAuthRepository, OAuthStorageError


router = APIRouter(prefix="/api/integrations", tags=["integrations"])
GRANT_PATTERN = re.compile(r"^grant_[a-f0-9]{32}$")
SUPPORTED_SCOPES = [
    {
        "scope": "forex:read",
        "label": "View Accounts and Markets",
        "description": "View trading accounts, strategies, schedules, and market information.",
    },
    {
        "scope": "forex:preview",
        "label": "Create Trade Previews",
        "description": "Create non-executing order previews and analysis results.",
    },
    {
        "scope": "forex:execute",
        "label": "Manage Demo Trading",
        "description": "Submit supported demo actions and manage automation safety controls.",
    },
]


def _safe_setup_url() -> str | None:
    value = settings.chatgpt_setup_url
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    return value


def _oauth_repository() -> OAuthRepository | None:
    try:
        return OAuthRepository()
    except (OAuthStorageError, OSError):
        return None


@router.get("/mcp")
async def mcp_settings(claims: dict = Depends(current_claims)) -> dict:
    repository = _oauth_repository()
    clients = repository.list_authorized_clients(claims["sub"]) if repository else None
    authentication_available = bool(
        settings.mcp_require_oauth
        and (settings.oauth_transaction_secret or settings.broker_secret_key)
    )
    return {
        "display_name": "Agentic Trading Desk",
        "server_url": CANONICAL_MCP_ENDPOINT,
        "resource_uri": CANONICAL_MCP_RESOURCE,
        "protected_resource_metadata_url": MCP_PROTECTED_RESOURCE_METADATA,
        "authorization_server_issuer": CANONICAL_MCP_RESOURCE,
        "authentication_required": True,
        "authentication_available": authentication_available,
        "status": "available" if authentication_available else "needs_attention",
        "supported_scopes": SUPPORTED_SCOPES,
        "unsupported_scopes": [{
            "scope": "trade:submit:live",
            "label": "Submit Live Trades",
            "description": "Live trade submission is not supported through this connection.",
        }],
        "authorized_clients": clients,
        "authorized_clients_status": "available" if repository else "unavailable",
        "revocation_supported": repository is not None,
        "setup_url": _safe_setup_url(),
        "protocol_status": "available",
    }


@router.post("/mcp/authorized-clients/{grant_id}/revoke")
async def revoke_mcp_client(
    grant_id: str, claims: dict = Depends(current_claims)
) -> dict:
    if not GRANT_PATTERN.fullmatch(grant_id):
        raise HTTPException(status_code=404, detail="Authorized application not found.")
    repository = _oauth_repository()
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail="Authorized application management is temporarily unavailable.",
        )
    result = repository.revoke_authorized_client(claims["sub"], grant_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Authorized application not found.")
    return {"status": "revoked", "grant_id": result["grant_id"]}
