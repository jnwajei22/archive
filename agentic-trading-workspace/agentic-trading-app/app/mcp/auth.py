import hmac
import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterable

import anyio
import jwt
from jwt import PyJWKClient
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config.settings import settings
from app.auth.identity import reset_current_claims, set_current_claims
from app.storage.oauth import OAuthRepository, OAuthStorageError
from app.oauth.constants import CANONICAL_MCP_RESOURCE


logger = logging.getLogger(__name__)
RESOURCE = CANONICAL_MCP_RESOURCE
RESOURCE_METADATA_URL = f"{RESOURCE}/.well-known/oauth-protected-resource"
OAUTH_CHALLENGE = f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
TOOL_SCOPES = {
    "get_forex_watchlist": "forex:read",
    "get_market_candles": "forex:read",
    "render_market_chart": "forex:read",
    "get_watchlist_market_data": "forex:read",
    "get_economic_calendar": "forex:read",
    "get_market_news": "forex:read",
    "search_macro_series": "forex:read",
    "get_macro_series": "forex:read",
    "get_macro_release_calendar": "forex:read",
    "get_forex_research_bundle": "forex:read",
    "get_provider_capabilities": "forex:read",
    "review_forex_order": "forex:preview",
    "get_account_status": "forex:read",
    "get_paper_account_status": "forex:read",
    "get_open_positions": "forex:read",
    "get_pending_orders": "forex:read",
    "get_trade_history": "forex:read",
    "set_kill_switch": "forex:execute",
    "start_autonomous_trading": "forex:execute",
    "stop_autonomous_trading": "forex:execute",
    "emergency_stop_autonomous_trading": "forex:execute",
    "get_autonomous_trading_status": "forex:read",
    "get_tradelocker_connection_status": "forex:read",
    "get_my_broker_connection_status": "forex:read",
    "get_my_tradelocker_accounts": "forex:read",
    "list_my_tradelocker_connections":"forex:read",
    "list_my_tradelocker_accounts":"forex:read",
    "list_execution_profiles":"forex:read",
    "get_my_tradelocker_account_status": "forex:read",
    "get_my_tradelocker_symbols": "forex:read",
    "get_my_tradelocker_quote": "forex:read",
    "get_my_tradelocker_candles": "forex:read",
    "get_tradelocker_accounts": "forex:read",
    "get_tradelocker_config": "forex:read",
    "get_tradelocker_symbols": "forex:read",
    "get_tradelocker_quote": "forex:read",
    "get_autonomous_demo_status": "forex:read",
    "get_autonomous_demo_snapshot": "forex:read",
    "review_autonomous_demo_order": "forex:preview",
    "submit_autonomous_demo_order": "forex:execute",
    "record_autonomous_no_trade": "forex:preview",
    "get_autonomous_run_result": "forex:read",
    "run_autonomous_demo_profile":"forex:execute",
    "list_autonomous_schedules":"forex:read",
    "get_autonomous_schedule_status":"forex:read",
    "list_recent_autonomous_runs":"forex:read",
    "get_autonomous_daily_summary":"forex:read",
    "get_demo_execution_status":"forex:read",
    "get_demo_trading_snapshot":"forex:read",
    "review_demo_order":"forex:preview",
    "submit_demo_order":"forex:execute",
    "get_demo_execution_result":"forex:read",
    "review_cancel_demo_order":"forex:preview",
    "submit_cancel_demo_order":"forex:execute",
    "review_close_demo_position":"forex:preview",
    "submit_close_demo_position":"forex:execute",
}

_jwks_clients: dict[str, PyJWKClient] = {}


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


def _bearer_token(scope: Scope) -> tuple[str | None, str | None]:
    raw = _header(scope, b"authorization")
    if raw is None or not raw.strip():
        return None, "missing_authorization_header"
    parts = raw.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None, "malformed_bearer_header"
    return parts[1], None


def _token_fingerprint(token: str | None) -> str:
    return hashlib.sha256((token or "<none>").encode()).hexdigest()[:12]


def _log_rejection(category: str, token: str | None = None, **fields: str) -> None:
    suffix = " ".join(f"{key}={value}" for key,value in fields.items())
    logger.warning("mcp_oauth_rejected reason=%s token_fp=%s%s",category,
        _token_fingerprint(token),f" {suffix}" if suffix else "")


def _jwt_failure_category(exc: jwt.PyJWTError) -> str:
    if isinstance(exc,jwt.ExpiredSignatureError):return "token_expired"
    if isinstance(exc,jwt.InvalidIssuerError):return "issuer_mismatch"
    if isinstance(exc,jwt.InvalidAudienceError):return "audience_resource_mismatch"
    if isinstance(exc,jwt.InvalidSignatureError):return "invalid_signature"
    return "other_validation_error"


def _verify_access_token(token: str) -> dict:
    if not settings.auth_issuer or not settings.auth_jwks_url:
        raise RuntimeError("OAuth issuer and JWKS URL must be configured.")
    client = _jwks_clients.setdefault(
        settings.auth_jwks_url, PyJWKClient(settings.auth_jwks_url)
    )
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
        audience=settings.auth_audience or RESOURCE,
        issuer=settings.auth_issuer,
        options={"require": ["exp", "iss", "aud"]},
    )


def _token_scopes(claims: dict) -> set[str]:
    value = claims.get("scope", claims.get("scp", ""))
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, Iterable):
        return {str(scope) for scope in value}
    return set()


async def _read_body(receive: Receive) -> tuple[bytes, Receive]:
    body = bytearray()
    more_body = True
    while more_body:
        message = await receive()
        body.extend(message.get("body", b""))
        more_body = message.get("more_body", False)

    delivered = False

    async def replay() -> Message:
        nonlocal delivered
        if delivered:
            return await receive()
        delivered = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    return bytes(body), replay


def _required_scopes(body: bytes) -> set[str]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return set()
    requests = payload if isinstance(payload, list) else [payload]
    return {
        TOOL_SCOPES[name]
        for request in requests
        if isinstance(request, dict) and request.get("method") == "tools/call"
        if isinstance(request.get("params"), dict)
        if (name := request["params"].get("name")) in TOOL_SCOPES
    }


async def _json_response(
    scope: Scope, receive: Receive, send: Send, detail: str, status_code: int, challenge: str
) -> None:
    await JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers={"WWW-Authenticate": challenge},
    )(scope, receive, send)


class MCPAuthMiddleware:
    """Protect the remote MCP transport with OAuth or an explicit test-only mode."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        if settings.mcp_allow_public_no_auth:
            logger.warning("MCP public no-auth mode is enabled for manual testing.")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            scope.get("path") == "/mcp" or scope.get("path", "").startswith("/mcp/")
        ):
            await self.app(scope, receive, send)
            return

        if not settings.mcp_require_oauth:
            if settings.mcp_allow_public_no_auth:
                await self.app(scope, receive, send)
                return
            token, _ = _bearer_token(scope)
            if settings.mcp_shared_secret and token and hmac.compare_digest(
                token, settings.mcp_shared_secret
            ):
                await self.app(scope, receive, send)
                return
            await _json_response(
                scope, receive, send, "Invalid or missing MCP bearer token.", 401, "Bearer"
            )
            return

        token, header_error = _bearer_token(scope)
        if not token:
            _log_rejection(header_error or "missing_authorization_header")
            await _json_response(
                scope, receive, send, "OAuth access token is required.", 401, OAUTH_CHALLENGE
            )
            return

        try:
            token_state, claims = await anyio.to_thread.run_sync(
                lambda: OAuthRepository().access_token_status(token, RESOURCE)
            )
        except (OAuthStorageError, sqlite3.Error, OSError):
            _log_rejection("token_lookup_unavailable",token)
            await JSONResponse({"detail":"OAuth token validation is temporarily unavailable."},status_code=503)(scope,receive,send)
            return
        try:
            if claims is None and token.count(".") == 2:
                claims = await anyio.to_thread.run_sync(_verify_access_token, token)
            elif claims is None:
                category = "unknown_token_fingerprint" if token_state == "token_record_not_found" else token_state
                _log_rejection(category,token,storage_reason=token_state)
                await _json_response(scope,receive,send,"Invalid OAuth access token.",401,OAUTH_CHALLENGE)
                return
        except RuntimeError as exc:
            logger.error("MCP OAuth configuration error: %s", exc)
            await JSONResponse({"detail": str(exc)}, status_code=503)(scope, receive, send)
            return
        except jwt.PyJWTError as exc:
            _log_rejection(_jwt_failure_category(exc),token)
            await _json_response(
                scope, receive, send, "Invalid OAuth access token.", 401, OAUTH_CHALLENGE
            )
            return

        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            _log_rejection("subject_missing",token)
            await _json_response(
                scope, receive, send, "OAuth token is missing the subject claim.", 401, OAUTH_CHALLENGE
            )
            return

        body, replay = await _read_body(receive)
        required = _required_scopes(body)
        missing = required - _token_scopes(claims)
        if missing:
            required_scope = " ".join(sorted(missing))
            challenge = f'{OAUTH_CHALLENGE}, error="insufficient_scope", scope="{required_scope}"'
            await _json_response(
                scope, replay, send, "OAuth token has insufficient scope.", 403, challenge
            )
            return

        identity_token = set_current_claims(claims)
        try:
            await self.app(scope, replay, send)
        finally:
            reset_current_claims(identity_token)
