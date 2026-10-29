import base64
import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import jwt
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.api.routes import oauth, platform
from app.config.settings import settings
from app.main import app
from app.storage.brokers import BrokerRepository
from app.storage.oauth import OAuthRepository
from app.oauth.cimd import CIMDMetadata
from app.oauth.constants import CANONICAL_MCP_RESOURCE


@pytest.fixture
def oauth_storage(tmp_path, monkeypatch):
    db_path = tmp_path / "oauth.db"
    monkeypatch.setattr(settings, "sqlite_path", str(db_path))
    monkeypatch.setattr(settings, "broker_secret_key", "test-broker-secret")
    monkeypatch.setattr(settings, "oauth_transaction_secret", "test-oauth-secret")
    monkeypatch.setattr(settings, "frontend_origin", "https://portal.example.test")
    monkeypatch.setattr(settings, "public_base_url", "https://mcp.example.test")
    monkeypatch.setattr(settings, "oauth_allowed_client_ids", "chatgpt-client")
    monkeypatch.setattr(settings, "onboarding_assertion_secret", "test-onboarding-assertion-secret")
    monkeypatch.setattr(settings, "onboarding_assertion_issuers", "https://portal.example.test")
    return BrokerRepository()


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def claims(subject="auth0|user-a"):
    return {"sub": subject, "email": f"{subject.replace('|', '-')}@example.test"}


def authenticate(subject="auth0|user-a"):
    app.dependency_overrides[platform.current_claims] = lambda: claims(subject)


def onboarding_assertion(transaction, subject="auth0|user-a", nonce=None):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "iss": "https://portal.example.test",
            "aud": "https://mcp.example.test/api/oauth/onboarding",
            "iat": now,
            "exp": now + timedelta(seconds=60),
            "jti": nonce or str(uuid.uuid4()),
            "tx_hash": hashlib.sha256(transaction.encode()).hexdigest(),
            "typ": "onboarding",
        },
        settings.onboarding_assertion_secret,
        algorithm="HS256",
    )


def onboarding_post(client, path, transaction, body=None, subject="auth0|user-a"):
    assertion = onboarding_assertion(transaction, subject)
    return client.post(
        path,
        json={"transaction": transaction, **(body or {})},
        headers={"Authorization": f"Onboarding {assertion}"},
    )


class FakeTradeLockerClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_accounts(self):
        return {"accounts": [{"accountId": 12345, "accNum": 2, "name": "Primary"}]}


def begin_authorization(client: TestClient, verifier="oauth-pkce-verifier", *,
                        client_id="chatgpt-client",
                        redirect_uri="https://chatgpt.com/aip/callback?existing=1",
                        resource=CANONICAL_MCP_RESOURCE,
                        scope="forex:read forex:preview"):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": "original-oauth-state",
            "scope": scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://portal.example.test/oauth/start"
    return parse_qs(location.query)["transaction"][0], verifier


def test_authorize_normalizes_canonical_resource_trailing_slash(oauth_storage):
    with TestClient(app) as client:
        transaction, _ = begin_authorization(
            client, resource=f"{CANONICAL_MCP_RESOURCE}/"
        )
    assert transaction


def test_first_time_oauth_stays_pending_until_tradelocker_setup(
    oauth_storage, monkeypatch
):
    monkeypatch.setattr(platform, "TradeLockerClient", FakeTradeLockerClient)
    authenticate()
    with TestClient(app) as client:
        transaction, verifier = begin_authorization(client)
        assert onboarding_post(client, "/api/oauth/onboarding/bind", transaction).json() == {"status": "bound"}
        initial = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
        assert initial["status"] == "not_connected"

        client.post("/api/broker/tradelocker/save-credentials", json={
            "base_url": "https://demo.example/backend-api", "username": "user-a",
            "password": "private-password", "server": "DEMO",
        })
        pending = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
        assert pending["status"] == "connected_no_account"
        discovered = client.post("/api/broker/tradelocker/discover-accounts").json()
        assert discovered["accounts"][0]["accountId"] == 12345
        client.post("/api/broker/tradelocker/select-account", json={"accountId": "12345", "accNum": "2"})
        ready = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
        assert ready["status"] == "ready"

        completed = onboarding_post(
            client, "/api/oauth/onboarding/complete", transaction,
            {"csrf_token": ready["csrf_token"]},
        )
        callback = urlparse(completed.json()["redirect_url"])
        callback_query = parse_qs(callback.query)
        assert f"{callback.scheme}://{callback.netloc}{callback.path}" == "https://chatgpt.com/aip/callback"
        assert callback_query["existing"] == ["1"]
        assert callback_query["state"] == ["original-oauth-state"]
        code = callback_query["code"][0]

        rejected = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": "chatgpt-client",
            "redirect_uri": "https://chatgpt.com/aip/callback?existing=1",
            "code_verifier": "incorrect-pkce-verifier",
            "resource": CANONICAL_MCP_RESOURCE,
        })
        assert rejected.status_code == 400
        wrong_resource = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": "chatgpt-client",
            "redirect_uri": "https://chatgpt.com/aip/callback?existing=1",
            "code_verifier": verifier, "resource": "https://wrong.example",
        })
        assert wrong_resource.status_code == 400
        token = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "client_id": "chatgpt-client",
            "redirect_uri": "https://chatgpt.com/aip/callback?existing=1",
            "code_verifier": verifier,
            "resource": CANONICAL_MCP_RESOURCE,
        })
        assert token.status_code == 200
        assert token.json()["token_type"] == "Bearer"
        assert token.json()["expires_in"] == settings.oauth_access_token_ttl_seconds
        assert token.json()["refresh_token"]
        assert "private-password" not in token.text
        mcp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "chatgpt-test", "version": "1"},
                },
            },
            headers={
                "Authorization": f"Bearer {token.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert mcp.status_code == 200
        preview_cannot_submit = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 12, "method": "tools/call",
                "params": {"name": "submit_demo_order", "arguments": {}},
            },
            headers={
                "Authorization": f"Bearer {token.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert preview_cannot_submit.status_code == 403
        assert 'scope="forex:execute"' in preview_cannot_submit.headers["www-authenticate"]
        persisted_state, persisted_claims = OAuthRepository().access_token_status(
            token.json()["access_token"], CANONICAL_MCP_RESOURCE
        )
        assert persisted_state == "accepted"
        assert persisted_claims["sub"] == "auth0|user-a"
        assert persisted_claims["scope"] == "forex:read forex:preview"
        refreshed = client.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": token.json()["refresh_token"],
            "client_id": "chatgpt-client",
        })
        assert refreshed.status_code == 200
        assert refreshed.json()["refresh_token"] != token.json()["refresh_token"]
        refreshed_mcp = client.post(
            "/mcp", json={
                "jsonrpc": "2.0", "id": 2, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "chatgpt-test", "version": "1"}},
            },
            headers={
                "Authorization": f"Bearer {refreshed.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert refreshed_mcp.status_code == 200
        reused = client.post("/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": token.json()["refresh_token"],
            "client_id": "chatgpt-client",
        })
        assert reused.status_code == 400
        assert reused.json() == {"error": "invalid_grant"}

        upgraded_transaction, upgraded_verifier = begin_authorization(
            client,
            verifier="upgraded-oauth-pkce-verifier-with-sufficient-entropy",
            scope="openid email forex:read forex:preview forex:execute",
        )
        assert onboarding_post(
            client, "/api/oauth/onboarding/bind", upgraded_transaction
        ).status_code == 200
        upgraded_status = onboarding_post(
            client, "/api/oauth/onboarding/status", upgraded_transaction
        ).json()
        assert upgraded_status["status"] == "ready"
        upgraded_completed = onboarding_post(
            client, "/api/oauth/onboarding/complete", upgraded_transaction,
            {"csrf_token": upgraded_status["csrf_token"]},
        ).json()
        upgraded_code = parse_qs(
            urlparse(upgraded_completed["redirect_url"]).query
        )["code"][0]
        upgraded = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": upgraded_code,
            "client_id": "chatgpt-client",
            "redirect_uri": "https://chatgpt.com/aip/callback?existing=1",
            "code_verifier": upgraded_verifier,
            "resource": f"{CANONICAL_MCP_RESOURCE}/",
        })
        assert upgraded.status_code == 200
        assert upgraded.json()["scope"] == (
            "openid email forex:read forex:preview forex:execute"
        )
        upgraded_submission_scope = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0", "id": 13, "method": "tools/call",
                "params": {"name": "submit_demo_order", "arguments": {}},
            },
            headers={
                "Authorization": f"Bearer {upgraded.json()['access_token']}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert upgraded_submission_scope.status_code == 200


def test_oauth_rejects_invalid_callback(oauth_storage):
    with TestClient(app) as client:
        response = client.get("/oauth/authorize", params={
            "client_id": "chatgpt-client", "redirect_uri": "https://evil.example/callback",
            "response_type": "code", "state": "state", "scope": "forex:read",
            "code_challenge": "a" * 43, "code_challenge_method": "S256",
            "resource": CANONICAL_MCP_RESOURCE,
        })
    assert response.status_code == 400


def test_arbitrary_static_client_is_rejected(oauth_storage):
    with TestClient(app) as client:
        response = client.get("/oauth/authorize", params={
            "client_id": "not-allowed", "redirect_uri": "https://chatgpt.com/aip/callback",
            "response_type": "code", "state": "state", "scope": "forex:read",
            "code_challenge": "a" * 43, "code_challenge_method": "S256",
            "resource": CANONICAL_MCP_RESOURCE,
        })
    assert response.status_code == 400
    assert "Static OAuth client is not allowed" in response.text


def test_cimd_identity_and_exact_redirect_survive_token_exchange(oauth_storage, monkeypatch):
    cimd_url = "https://chatgpt.com/oauth/agentic-forex/client.json"
    callback = "https://chatgpt.com/connector/oauth/callback-id"
    monkeypatch.setattr(oauth.cimd_loader, "load", AsyncMock(return_value=CIMDMetadata(
        client_id=cimd_url, redirect_uris=(callback,), token_endpoint_auth_methods=("none",),
    )))
    monkeypatch.setattr(platform, "TradeLockerClient", FakeTradeLockerClient)
    oauth_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="private-password", server="DEMO",
    )
    oauth_storage.select_account("auth0|user-a", "12345", "2")
    authenticate()
    with TestClient(app) as client:
        transaction, verifier = begin_authorization(
            client, client_id=cimd_url, redirect_uri=callback,
        )
        onboarding_post(client, "/api/oauth/onboarding/bind", transaction)
        status = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
        completed = onboarding_post(
            client, "/api/oauth/onboarding/complete", transaction,
            {"csrf_token": status["csrf_token"]},
        ).json()
        query = parse_qs(urlparse(completed["redirect_url"]).query)
        token = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": query["code"][0],
            "client_id": cimd_url, "redirect_uri": callback,
            "code_verifier": verifier, "resource": CANONICAL_MCP_RESOURCE,
        })
    assert token.status_code == 200
    oauth.cimd_loader.load.assert_awaited_once_with(cimd_url)


def test_cimd_unknown_redirect_is_rejected(oauth_storage, monkeypatch):
    cimd_url = "https://chatgpt.com/oauth/agentic-forex/client.json"
    monkeypatch.setattr(oauth.cimd_loader, "load", AsyncMock(return_value=CIMDMetadata(
        client_id=cimd_url,
        redirect_uris=("https://chatgpt.com/connector/oauth/registered",),
        token_endpoint_auth_methods=("none",),
    )))
    with TestClient(app) as client:
        response = client.get("/oauth/authorize", params={
            "client_id": cimd_url,
            "redirect_uri": "https://chatgpt.com/connector/oauth/unknown",
            "response_type": "code", "state": "state", "scope": "forex:read",
            "code_challenge": "a" * 43, "code_challenge_method": "S256",
            "resource": CANONICAL_MCP_RESOURCE,
        })
    assert response.status_code == 400


def test_existing_configured_user_skips_to_completion(oauth_storage, monkeypatch):
    monkeypatch.setattr(platform, "TradeLockerClient", FakeTradeLockerClient)
    oauth_storage.save_connection(
        "auth0|user-a", base_url="https://demo.example/backend-api",
        username="user-a", password="private-password", server="DEMO",
    )
    oauth_storage.select_account("auth0|user-a", "12345", "2")
    authenticate()
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        assert onboarding_post(client, "/api/oauth/onboarding/bind", transaction).status_code == 200
        status = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
    assert status["status"] == "ready"
    assert status["selected_account"]["account_id"] == "12345"


def test_oauth_rejects_incomplete_setup_and_wrong_user(oauth_storage):
    authenticate("auth0|user-a")
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        onboarding_post(client, "/api/oauth/onboarding/bind", transaction)
        status = onboarding_post(client, "/api/oauth/onboarding/status", transaction).json()
        incomplete = onboarding_post(
            client, "/api/oauth/onboarding/complete", transaction,
            {"csrf_token": status["csrf_token"]},
        )
        assert incomplete.status_code == 409
        authenticate("auth0|user-b")
        wrong_user = onboarding_post(
            client, "/api/oauth/onboarding/complete", transaction,
            {"csrf_token": status["csrf_token"]}, subject="auth0|user-b",
        )
        assert wrong_user.status_code == 403
        assert wrong_user.json()["detail"]["error"] == "onboarding_owner_mismatch"


def test_oauth_rejects_expired_transaction(oauth_storage):
    authenticate()
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        with sqlite3.connect(settings.sqlite_path) as connection:
            connection.execute("UPDATE oauth_transactions SET expires_at = '2000-01-01T00:00:00+00:00'")
        response = onboarding_post(client, "/api/oauth/onboarding/bind", transaction)
    assert response.status_code == 410


def test_onboarding_route_rejects_missing_assertion_and_transaction(oauth_storage):
    authenticate()
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        missing_assertion = client.post(
            "/api/oauth/onboarding/status", json={"transaction": transaction}
        )
        missing_transaction = onboarding_post(
            client, "/api/oauth/onboarding/status", ""
        )
    assert missing_assertion.status_code == 401
    assert missing_assertion.json()["detail"]["error"] == "onboarding_assertion_required"
    assert missing_transaction.status_code == 401
    assert missing_transaction.json()["detail"]["error"] == "onboarding_transaction_required"


def test_onboarding_assertion_nonce_cannot_be_replayed(oauth_storage):
    authenticate()
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        assertion = onboarding_assertion(transaction, nonce="one-use-nonce")
        headers = {"Authorization": f"Onboarding {assertion}"}
        first = client.post(
            "/api/oauth/onboarding/bind",
            json={"transaction": transaction}, headers=headers,
        )
        replay = client.post(
            "/api/oauth/onboarding/status",
            json={"transaction": transaction}, headers=headers,
        )
    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["detail"]["error"] == "onboarding_assertion_replayed"


def test_transaction_owner_uses_exact_auth0_sub_and_is_not_overwritten(oauth_storage):
    subject = "auth0|CaseSensitiveSubject"
    authenticate(subject)
    with TestClient(app) as client:
        transaction, _ = begin_authorization(client)
        first = onboarding_post(
            client, "/api/oauth/onboarding/bind", transaction, subject=subject,
        )
        second = onboarding_post(
            client, "/api/oauth/onboarding/bind", transaction, subject=subject,
        )
    assert first.status_code == 200
    assert second.status_code == 200
    with sqlite3.connect(settings.sqlite_path) as connection:
        stored = connection.execute(
            "SELECT user_sub, status FROM oauth_transactions"
        ).fetchone()
        count = connection.execute("SELECT COUNT(*) FROM oauth_transactions").fetchone()[0]
    assert stored == (subject, "AUTH0_COMPLETE")
    assert stored[0] != f"{subject.replace('|', '-')}@example.test"
    assert count == 1
