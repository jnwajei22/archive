from __future__ import annotations

import re
from typing import Any


_SECRET_KEYS = re.compile(
    r"(password|secret|token|api[_-]?key|authorization|credential|encryption[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*")


def redact(value: Any) -> Any:
    """Recursively produce a persistence/log-safe copy of untrusted data."""
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEYS.search(str(key)) else redact(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub("Bearer [REDACTED]", value)
    return value


def safe_error(exc: BaseException) -> str:
    """Return a stable category without reflecting provider payloads or secrets."""
    return f"{type(exc).__name__}: operation failed; inspect redacted server logs"
