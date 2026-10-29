import json

import httpx
import pytest

from app.oauth.cimd import CIMDError, CIMDLoader, MAX_CIMD_BYTES, normalize_cimd_url


PUBLIC_RESOLVER = lambda host, port: _public_address()


async def _public_address():
    return ["93.184.216.34"]


def document(**overrides):
    value = {
        "redirect_uris": ["https://chatgpt.com/connector/oauth/callback-id"],
        "response_types": ["code"],
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
    }
    value.update(overrides)
    return value


def loader_for(content: bytes, *, content_type="application/json", status=200):
    async def handler(request):
        return httpx.Response(status, content=content, headers={"content-type": content_type})
    return CIMDLoader(transport=httpx.MockTransport(handler), resolver=PUBLIC_RESOLVER)


@pytest.mark.asyncio
async def test_valid_chatgpt_cimd_document_is_loaded_and_cached():
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=document())
    loader = CIMDLoader(transport=httpx.MockTransport(handler), resolver=PUBLIC_RESOLVER)
    first = await loader.load("https://chatgpt.com/oauth/client.json")
    second = await loader.load("https://chatgpt.com/oauth/client.json")
    assert first.client_id == "https://chatgpt.com/oauth/client.json"
    assert first.redirect_uris == ("https://chatgpt.com/connector/oauth/callback-id",)
    assert first.grant_types == ("authorization_code", "refresh_token")
    assert second == first
    assert calls == 1


@pytest.mark.parametrize("value", [
    "http://chatgpt.com/client.json", "https://localhost/client.json",
    "https://user:password@chatgpt.com/client.json", "https://chatgpt.com/client.json#fragment",
])
def test_cimd_url_rejects_unsafe_forms(value):
    with pytest.raises(CIMDError):
        normalize_cimd_url(value)


@pytest.mark.asyncio
async def test_cimd_rejects_private_dns_resolution():
    async def private(host, port):
        return ["10.0.0.2"]
    loader = CIMDLoader(transport=httpx.MockTransport(lambda request: httpx.Response(200)), resolver=private)
    with pytest.raises(CIMDError, match="disallowed network"):
        await loader.load("https://client.example/metadata.json")


@pytest.mark.asyncio
async def test_cimd_rejects_malformed_json_and_oversized_documents():
    with pytest.raises(CIMDError, match="malformed JSON"):
        await loader_for(b"not-json").load("https://client.example/metadata.json")
    with pytest.raises(CIMDError, match="too large"):
        await loader_for(b"x" * (MAX_CIMD_BYTES + 1)).load("https://client.example/metadata.json")


@pytest.mark.asyncio
async def test_cimd_rejects_unsupported_auth_method():
    payload = json.dumps(document(token_endpoint_auth_methods_supported=["client_secret_basic"])).encode()
    with pytest.raises(CIMDError, match="compatible token authentication"):
        await loader_for(payload).load("https://client.example/metadata.json")
