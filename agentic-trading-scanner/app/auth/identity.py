from contextvars import ContextVar, Token
from typing import Any


_current_claims: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_auth0_claims", default=None
)


def set_current_claims(claims: dict[str, Any]) -> Token:
    return _current_claims.set(claims)


def reset_current_claims(token: Token) -> None:
    _current_claims.reset(token)


def get_current_claims() -> dict[str, Any] | None:
    return _current_claims.get()


def get_current_user_sub() -> str | None:
    # Local autonomous mode has one server-owned identity. Caller input never
    # participates in this resolution.
    from app.config.settings import settings
    if settings.is_local_autonomous:
        return settings.local_user_id
    claims = get_current_claims()
    subject = claims.get("sub") if claims else None
    return subject if isinstance(subject, str) and subject else None


def local_principal() -> dict[str, Any] | None:
    from app.config.settings import settings
    if not settings.is_local_autonomous:
        return None
    return {"sub": settings.local_user_id, "user_id": settings.local_user_id,
            "auth_mode": "local_autonomous", "is_local": True,
            "scope": "forex:read forex:preview forex:execute"}


def normalize_auth0_subject(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Auth0 subject is required.")
    return value.strip()
