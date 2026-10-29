import hashlib
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException

from app.config.settings import settings
from app.storage.oauth import OAuthRepository, OAuthStorageError
from app.auth.identity import normalize_auth0_subject


ASSERTION_ALGORITHM = "HS256"


def onboarding_audience() -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/oauth/onboarding"


def transaction_digest(reference: str) -> str:
    return hashlib.sha256(reference.encode()).hexdigest()


async def current_onboarding_claims(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "onboarding" or not token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "onboarding_assertion_required",
                "message": "A server-side onboarding assertion is required.",
            },
        )
    if not settings.onboarding_assertion_secret:
        raise HTTPException(status_code=503, detail="ONBOARDING_ASSERTION_SECRET is not configured.")
    try:
        claims = jwt.decode(
            token,
            settings.onboarding_assertion_secret,
            algorithms=[ASSERTION_ALGORITHM],
            audience=onboarding_audience(),
            options={"require": ["sub", "aud", "iss", "iat", "exp", "jti", "tx_hash"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_onboarding_assertion", "message": "The onboarding assertion is invalid or expired."},
        ) from None
    allowed_issuers = {
        item.strip().rstrip("/")
        for item in (settings.onboarding_assertion_issuers or settings.frontend_origin).split(",")
        if item.strip()
    }
    if (claims.get("typ") != "onboarding" or not isinstance(claims.get("sub"), str)
            or claims.get("iss", "").rstrip("/") not in allowed_issuers):
        raise HTTPException(status_code=401, detail={"error": "invalid_onboarding_assertion"})
    try:
        claims["sub"] = normalize_auth0_subject(claims["sub"])
    except ValueError:
        raise HTTPException(status_code=401, detail={"error": "invalid_onboarding_identity"}) from None
    try:
        expires_at = datetime.fromtimestamp(float(claims["exp"]), timezone.utc)
        consumed = OAuthRepository().consume_onboarding_assertion_nonce(str(claims["jti"]), expires_at)
    except (OAuthStorageError, TypeError, ValueError):
        raise HTTPException(status_code=503, detail="Onboarding assertion verification is unavailable.") from None
    if not consumed:
        raise HTTPException(
            status_code=401,
            detail={"error": "onboarding_assertion_replayed", "message": "The onboarding assertion was already used."},
        )
    return claims
