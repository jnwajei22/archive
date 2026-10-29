from __future__ import annotations


CANONICAL_MCP_RESOURCE = "https://mcp.justinnwajei.com"
CANONICAL_MCP_ENDPOINT = f"{CANONICAL_MCP_RESOURCE}/mcp"
MCP_PROTECTED_RESOURCE_METADATA = (
    f"{CANONICAL_MCP_RESOURCE}/.well-known/oauth-protected-resource"
)


def normalize_resource(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def canonical_resource(value: str | None) -> str | None:
    normalized = normalize_resource(value)
    return CANONICAL_MCP_RESOURCE if normalized == CANONICAL_MCP_RESOURCE else None
