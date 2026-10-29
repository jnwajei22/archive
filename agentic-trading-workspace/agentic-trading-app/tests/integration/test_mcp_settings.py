import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.routes import platform
from app.config.settings import settings
from app.main import app
from app.oauth.constants import CANONICAL_MCP_ENDPOINT, CANONICAL_MCP_RESOURCE
from app.storage.brokers import BrokerRepository
from app.storage.oauth import OAuthRepository


@pytest.fixture(autouse=True)
def mcp_settings_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "mcp-settings.db"))
    monkeypatch.setattr(settings, "broker_secret_key", "mcp-settings-test-secret")
    monkeypatch.setattr(settings, "oauth_transaction_secret", None)
    monkeypatch.setattr(settings, "mcp_require_oauth", True)
    monkeypatch.setattr(settings, "chatgpt_setup_url", None)
    yield
    app.dependency_overrides.clear()


def _client(subject: str) -> TestClient:
    app.dependency_overrides[platform.current_claims] = lambda: {"sub": subject}
    return TestClient(app)


def _authorize(subject: str, scopes: str = "forex:read forex:preview") -> dict:
    repository = OAuthRepository()
    with repository._connect() as connection:
        repository._issue_token_pair(
            connection,
            user_sub=subject,
            client_id="https://chatgpt.com/.well-known/oauth-client",
            scope=scopes,
            resource=CANONICAL_MCP_RESOURCE,
            now=datetime.now(timezone.utc),
        )
    return repository.list_authorized_clients(subject)[0]


def test_mcp_settings_response_is_public_user_safe_and_uses_canonical_metadata():
    response = _client("auth0|settings-user").get("/api/integrations/mcp")
    assert response.status_code == 200
    payload = response.json()
    assert payload["server_url"] == CANONICAL_MCP_ENDPOINT
    assert payload["resource_uri"] == CANONICAL_MCP_RESOURCE
    assert payload["server_url"].startswith("https://")
    assert payload["authorized_clients"] == []
    assert payload["status"] == "available"
    serialized = json.dumps(payload).lower()
    for forbidden in (
        "settings-user", "access_token", "refresh_token", "client_secret",
        "authorization_code", "code_verifier", "oauth_state", "broker_secret",
    ):
        assert forbidden not in serialized


def test_supported_scopes_are_normalized_to_customer_labels_without_live_grant():
    payload = _client("auth0|scope-user").get("/api/integrations/mcp").json()
    scopes = {item["scope"]: item["label"] for item in payload["supported_scopes"]}
    assert scopes == {
        "forex:read": "View Accounts and Markets",
        "forex:preview": "Create Trade Previews",
        "forex:execute": "Manage Demo Trading",
    }
    assert "live" not in " ".join(scopes.values()).lower()
    assert payload["unsupported_scopes"] == [{
        "scope": "trade:submit:live",
        "label": "Submit Live Trades",
        "description": "Live trade submission is not supported through this connection.",
    }]


def test_authorized_clients_are_user_scoped_and_revoke_is_idempotent():
    grant = _authorize("auth0|owner")
    _authorize("auth0|other", "forex:read")
    owner_payload = _client("auth0|owner").get("/api/integrations/mcp").json()
    assert [item["grant_id"] for item in owner_payload["authorized_clients"]] == [grant["grant_id"]]
    assert owner_payload["authorized_clients"][0]["client_name"] == "ChatGPT"
    assert owner_payload["authorized_clients"][0]["last_used_at"] is None

    path = f"/api/integrations/mcp/authorized-clients/{grant['grant_id']}/revoke"
    assert _client("auth0|other").post(path).status_code == 404
    assert _client("auth0|owner").post(path).json()["status"] == "revoked"
    assert _client("auth0|owner").post(path).json()["status"] == "revoked"
    assert OAuthRepository().list_authorized_clients("auth0|owner")[0]["status"] == "revoked"


def test_revocation_preserves_dashboard_broker_data_and_audits_only_safe_fields():
    subject = "auth0|broker-owner"
    brokers = BrokerRepository()
    saved = brokers.save_connection(
        subject, base_url="https://demo.example/backend-api", username="trader",
        password="broker-password", server="Broker", environment="demo",
    )
    grant = _authorize(subject, "forex:read forex:preview forex:execute")
    path = f"/api/integrations/mcp/authorized-clients/{grant['grant_id']}/revoke"
    assert _client(subject).post(path).status_code == 200
    assert brokers.get_connection(subject, saved.connection_ref) is not None
    audit = OAuthRepository().client_audit(subject)
    assert {item["event_type"] for item in audit} == {
        "mcp_application_connected", "mcp_application_disconnected"
    }
    serialized = json.dumps(audit).lower()
    assert "broker-password" not in serialized
    assert "access_token" not in serialized
    assert "client_id" not in serialized


def test_existing_mcp_discovery_and_resource_binding_remain_canonical():
    resource = _client("auth0|metadata-user").get(
        "/.well-known/oauth-protected-resource"
    ).json()
    assert resource["resource"] == CANONICAL_MCP_RESOURCE
    assert resource["authorization_servers"] == [CANONICAL_MCP_RESOURCE]
    assert OAuthRepository().access_token_status("not-a-token", CANONICAL_MCP_RESOURCE)[0] == "token_record_not_found"
