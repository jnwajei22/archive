import ipaddress
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import anyio
import httpx


logger = logging.getLogger(__name__)
MAX_CIMD_BYTES = 64 * 1024
CIMD_TIMEOUT_SECONDS = 4.0
CIMD_CACHE_SECONDS = 300
SUPPORTED_TOKEN_AUTH_METHODS = {"none"}
SUPPORTED_GRANT_TYPES = {"authorization_code", "refresh_token"}


class CIMDError(ValueError):
    pass


@dataclass(frozen=True)
class CIMDMetadata:
    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_methods: tuple[str, ...]
    grant_types: tuple[str, ...] = ("authorization_code",)


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def _resolve_addresses(hostname: str, port: int) -> list[str]:
    def resolve() -> list[str]:
        return list({item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)})
    return await anyio.to_thread.run_sync(resolve)


def _is_forbidden_address(value: str) -> bool:
    address = ipaddress.ip_address(value.split("%")[0])
    return bool(
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_reserved or address.is_unspecified
        or address == ipaddress.ip_address("169.254.169.254")
    )


def normalize_cimd_url(value: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(value)
        port = parsed.port or 443
    except ValueError as exc:
        raise CIMDError("CIMD client_id URL is invalid.") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise CIMDError("CIMD client_id must be an HTTPS URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise CIMDError("CIMD client_id must not contain credentials or a fragment.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise CIMDError("CIMD client_id host is not allowed.")
    normalized_netloc = hostname if port == 443 else f"{hostname}:{port}"
    normalized = urlunsplit(("https", normalized_netloc, parsed.path or "/", parsed.query, ""))
    return normalized, hostname, port


class CIMDLoader:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None,
                 resolver: Resolver = _resolve_addresses) -> None:
        self.transport = transport
        self.resolver = resolver
        self._cache: dict[str, tuple[float, CIMDMetadata]] = {}

    async def load(self, client_id: str) -> CIMDMetadata:
        normalized, hostname, port = normalize_cimd_url(client_id)
        cached = self._cache.get(normalized)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            addresses = await self.resolver(hostname, port)
        except OSError as exc:
            raise CIMDError("CIMD client host could not be resolved.") from exc
        if not addresses or any(_is_forbidden_address(address) for address in addresses):
            raise CIMDError("CIMD client host resolves to a disallowed network.")
        timeout = httpx.Timeout(CIMD_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=self.transport,
            headers={"Accept": "application/json"},
        ) as client:
            try:
                async with client.stream("GET", normalized) as response:
                    if response.is_redirect:
                        raise CIMDError("CIMD redirects are not allowed.")
                    if response.status_code != 200:
                        raise CIMDError("CIMD document could not be retrieved.")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    if content_type != "application/json" and not content_type.endswith("+json"):
                        raise CIMDError("CIMD document must use a JSON content type.")
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > MAX_CIMD_BYTES:
                        raise CIMDError("CIMD document is too large.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_CIMD_BYTES:
                            raise CIMDError("CIMD document is too large.")
            except httpx.HTTPError as exc:
                raise CIMDError("CIMD document could not be retrieved.") from exc
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CIMDError("CIMD document contains malformed JSON.") from exc
        metadata = self._validate(normalized, payload)
        self._cache[normalized] = (time.monotonic() + CIMD_CACHE_SECONDS, metadata)
        logger.info("Validated CIMD metadata host=%s redirect_count=%d", hostname, len(metadata.redirect_uris))
        return metadata

    @staticmethod
    def _validate(client_id: str, payload: Any) -> CIMDMetadata:
        if not isinstance(payload, dict):
            raise CIMDError("CIMD document must be a JSON object.")
        redirects = payload.get("redirect_uris")
        if (not isinstance(redirects, list) or not redirects
                or any(not isinstance(uri, str) or not uri.startswith("https://") for uri in redirects)):
            raise CIMDError("CIMD redirect_uris must contain HTTPS URLs.")
        response_types = payload.get("response_types", ["code"])
        grant_types = payload.get("grant_types", ["authorization_code"])
        if (not isinstance(response_types, list)
                or any(not isinstance(item, str) for item in response_types)
                or not isinstance(grant_types, list)
                or any(not isinstance(grant, str) for grant in grant_types)
                or any(grant not in SUPPORTED_GRANT_TYPES for grant in grant_types)
                or "code" not in response_types or "authorization_code" not in grant_types):
            raise CIMDError("CIMD client must support the authorization-code flow.")
        methods = payload.get(
            "token_endpoint_auth_methods_supported",
            [payload.get("token_endpoint_auth_method", "none")],
        )
        if not isinstance(methods, list) or not SUPPORTED_TOKEN_AUTH_METHODS.intersection(methods):
            raise CIMDError("CIMD client does not support a compatible token authentication method.")
        return CIMDMetadata(
            client_id=client_id,
            redirect_uris=tuple(redirects),
            token_endpoint_auth_methods=tuple(str(method) for method in methods),
            grant_types=tuple(str(grant) for grant in grant_types),
        )


cimd_loader = CIMDLoader()
