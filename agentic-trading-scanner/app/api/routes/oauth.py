from urllib.parse import parse_qs, urlencode, urlparse
import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.api.routes.platform import validated_onboarding_status
from app.config.settings import settings
from app.storage.oauth import OAuthRepository, OAuthStorageError
from app.oauth.cimd import CIMDError, cimd_loader
from app.auth.onboarding import current_onboarding_claims, transaction_digest
from app.oauth.constants import CANONICAL_MCP_RESOURCE, canonical_resource, normalize_resource


router = APIRouter(tags=["oauth"])
logger = logging.getLogger(__name__)
ALLOWED_CALLBACK_ORIGINS = {"https://chatgpt.com", "https://chat.openai.com"}
ALLOWED_SCOPES = {"openid", "profile", "email", "forex:read", "forex:preview", "forex:execute"}


def oauth_repository() -> OAuthRepository:
    try:
        return OAuthRepository()
    except OAuthStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _validate_callback(value: str) -> str:
    parsed = urlparse(value)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme != "https" or origin not in ALLOWED_CALLBACK_ORIGINS:
        raise HTTPException(status_code=400, detail="OAuth callback URL is not allowed.")
    return value


@router.get("/oauth/authorize")
async def authorize(
    client_id: str, redirect_uri: str, response_type: str, state: str,
    code_challenge: str, code_challenge_method: str, scope: str = "forex:read",
    nonce: str | None = None, resource: str | None = None,
) -> RedirectResponse:
    if (not client_id or not state or response_type != "code"
            or code_challenge_method != "S256" or not 43 <= len(code_challenge) <= 128):
        raise HTTPException(status_code=400, detail="Authorization code flow with PKCE S256 is required.")
    configured_clients = {item.strip() for item in (settings.oauth_allowed_client_ids or "").split(",") if item.strip()}
    if client_id.startswith(("https://", "http://")):
        try:
            client_metadata = await cimd_loader.load(client_id)
        except CIMDError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        client_id = client_metadata.client_id
        if redirect_uri not in client_metadata.redirect_uris:
            raise HTTPException(status_code=400, detail="OAuth redirect URI is not registered in the CIMD document.")
    elif client_id not in configured_clients:
        raise HTTPException(status_code=400, detail="Static OAuth client is not allowed.")
    requested_scopes = set(scope.split())
    if not requested_scopes or not requested_scopes <= ALLOWED_SCOPES:
        raise HTTPException(status_code=400, detail="Unsupported OAuth scope.")
    callback = _validate_callback(redirect_uri)
    expected_resource = canonical_resource(resource)
    if expected_resource is None:
        raise HTTPException(status_code=400, detail="OAuth resource is missing or invalid.")
    reference = oauth_repository().create_transaction(
        client_id=client_id, redirect_uri=callback, state=state, scope=scope,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method,
        resource=expected_resource, nonce=nonce,
    )
    frontend = settings.frontend_origin.rstrip("/")
    return RedirectResponse(f"{frontend}/oauth/start?{urlencode({'transaction': reference})}", status_code=302)


class TransactionRequest(BaseModel):
    transaction: str | None = None


class CompletionRequest(TransactionRequest):
    csrf_token: str


@router.post("/api/oauth/onboarding/bind")
async def bind_transaction(payload: TransactionRequest, claims: dict = Depends(current_onboarding_claims)) -> dict:
    reference = _require_transaction(payload.transaction)
    _validate_assertion_transaction(reference, claims)
    existing = oauth_repository().get_transaction(reference)
    if existing is None:
        raise HTTPException(status_code=410, detail="The ChatGPT sign-in request expired. Restart sign-in from ChatGPT.")
    if existing.user_sub and existing.user_sub != claims["sub"]:
        _log_owner_mismatch("bind", reference, claims["sub"], existing.user_sub)
        raise HTTPException(status_code=403, detail={
            "error": "onboarding_owner_mismatch",
            "message": "This onboarding transaction belongs to another authenticated user.",
        })
    transaction = oauth_repository().bind_user(reference, claims["sub"])
    if transaction is None:
        raise HTTPException(status_code=409, detail="The onboarding transaction could not be bound.")
    return {"status": "bound"}


@router.post("/api/oauth/onboarding/status")
async def oauth_onboarding_status(payload: TransactionRequest, claims: dict = Depends(current_onboarding_claims)) -> dict:
    reference = _require_transaction(payload.transaction)
    _validate_assertion_transaction(reference, claims)
    transaction = oauth_repository().get_transaction(reference)
    if transaction is None:
        raise HTTPException(status_code=410, detail="The ChatGPT sign-in request expired. Restart sign-in from ChatGPT.")
    if transaction.user_sub != claims["sub"]:
        _log_owner_mismatch("status", reference, claims["sub"], transaction.user_sub)
        raise HTTPException(status_code=403, detail={
            "error": "onboarding_owner_mismatch",
            "message": "This onboarding transaction belongs to another authenticated user.",
        })
    if transaction.status != "AUTH0_COMPLETE":
        raise HTTPException(status_code=409, detail="The onboarding transaction is not ready for status checks.")
    status = await validated_onboarding_status(claims["sub"])
    return {**status.model_dump(mode="json"), "transaction_valid": True,
            "csrf_token": transaction.csrf_token}


@router.post("/api/oauth/onboarding/complete")
async def complete_authorization(payload: CompletionRequest, claims: dict = Depends(current_onboarding_claims)) -> dict:
    reference = _require_transaction(payload.transaction)
    _validate_assertion_transaction(reference, claims)
    repo = oauth_repository()
    transaction = repo.get_transaction(reference)
    if transaction is None:
        raise HTTPException(status_code=410, detail="The ChatGPT sign-in request expired. Restart sign-in from ChatGPT.")
    if transaction.user_sub != claims["sub"]:
        _log_owner_mismatch("complete", reference, claims["sub"], transaction.user_sub)
        raise HTTPException(status_code=403, detail={
            "error": "onboarding_owner_mismatch",
            "message": "This onboarding transaction belongs to another authenticated user.",
        })
    if payload.csrf_token != transaction.csrf_token:
        raise HTTPException(status_code=403, detail="Invalid onboarding request.")
    status = await validated_onboarding_status(claims["sub"])
    if status.status != "ready":
        raise HTTPException(status_code=409, detail="A valid TradeLocker account must be selected before authorization.")
    issued = repo.issue_code(reference, claims["sub"])
    if issued is None:
        raise HTTPException(status_code=409, detail="The authorization request could not be completed.")
    code, original = issued
    separator = "&" if "?" in original.redirect_uri else "?"
    callback_url = f"{original.redirect_uri}{separator}{urlencode({'code': code, 'state': original.state})}"
    return {"redirect_url": callback_url}


def _validate_assertion_transaction(reference: str, claims: dict) -> None:
    if claims.get("tx_hash") != transaction_digest(reference):
        raise HTTPException(
            status_code=403,
            detail={"error": "onboarding_transaction_mismatch", "message": "The assertion does not match this onboarding transaction."},
        )


def _require_transaction(reference: str | None) -> str:
    if not reference:
        raise HTTPException(
            status_code=401,
            detail={"error": "onboarding_transaction_required", "message": "The onboarding transaction is missing."},
        )
    return reference


def _safe_fingerprint(value: str | None) -> str:
    return hashlib.sha256((value or "<none>").encode()).hexdigest()[:12]


def _log_owner_mismatch(route: str, reference: str, caller: str, owner: str | None) -> None:
    logger.warning(
        "OAuth onboarding owner mismatch route=%s transaction_fp=%s caller_fp=%s owner_fp=%s",
        route, _safe_fingerprint(reference), _safe_fingerprint(caller), _safe_fingerprint(owner),
    )


@router.post("/oauth/token")
async def exchange_token(request: Request) -> JSONResponse:
    form = {key: values[-1] for key, values in parse_qs((await request.body()).decode()).items()}
    grant_type = form.get("grant_type", "")
    client_id = form.get("client_id", "")
    if grant_type == "authorization_code":
        result = oauth_repository().exchange_code(
            code=form.get("code", ""), client_id=client_id,
            redirect_uri=form.get("redirect_uri", ""),
            code_verifier=form.get("code_verifier", ""),
            resource=form.get("resource", ""),
        )
    elif grant_type == "refresh_token":
        requested_resource=form.get("resource")
        result = oauth_repository().exchange_refresh_token(
            refresh_token=form.get("refresh_token", ""), client_id=client_id,
            resource=normalize_resource(requested_resource) if requested_resource else CANONICAL_MCP_RESOURCE,
            scope=form.get("scope"),
        )
    else:
        _log_token_lifecycle(grant_type or "missing", client_id, "rejected", False, "unsupported_grant_type")
        return _token_response({"error": "unsupported_grant_type"}, status_code=400)
    if result is None:
        _log_token_lifecycle(grant_type, client_id, "rejected", False, "invalid_grant")
        return _token_response({"error": "invalid_grant"}, status_code=400)
    _log_token_lifecycle(grant_type, client_id, "issued", True, None)
    return _token_response(result)


def _token_response(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content, status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _log_token_lifecycle(
    grant_type: str, client_id: str, result: str,
    refresh_token_issued: bool, failure_category: str | None,
) -> None:
    logger.info(
        "OAuth token grant_type=%s result=%s client_fp=%s access_token_ttl=%s "
        "refresh_token_issued=%s failure_category=%s",
        grant_type, result, _safe_fingerprint(client_id),
        settings.oauth_access_token_ttl_seconds, refresh_token_issued,
        failure_category,
    )
