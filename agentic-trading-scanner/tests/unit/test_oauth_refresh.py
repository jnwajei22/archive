import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config.settings import settings
from app.storage.brokers import BrokerRepository
from app.storage.oauth import OAuthRepository
from app.oauth.constants import CANONICAL_MCP_RESOURCE


RESOURCE = CANONICAL_MCP_RESOURCE
CLIENT = "chatgpt-client"
USER = "auth0|refresh-user"
VERIFIER = "refresh-pkce-verifier-with-sufficient-entropy"


@pytest.fixture
def repository(tmp_path, monkeypatch):
    db_path = tmp_path / "oauth.db"
    monkeypatch.setattr(settings, "oauth_access_token_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "oauth_refresh_token_ttl_seconds", 7776000)
    monkeypatch.setattr(settings, "sqlite_path", str(db_path))
    monkeypatch.setattr(settings, "oauth_transaction_secret", "oauth-test-secret")
    monkeypatch.setattr(settings, "public_base_url", RESOURCE)
    return OAuthRepository(db_path, secret="oauth-test-secret")


def issue_pair(repository: OAuthRepository, scope="forex:read forex:preview"):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    reference = repository.create_transaction(
        client_id=CLIENT, redirect_uri="https://chatgpt.com/aip/callback", state="state",
        scope=scope, code_challenge=challenge, code_challenge_method="S256",
        resource=RESOURCE,
    )
    assert repository.bind_user(reference, USER)
    code, _ = repository.issue_code(reference, USER)
    result = repository.exchange_code(
        code=code, client_id=CLIENT, redirect_uri="https://chatgpt.com/aip/callback",
        code_verifier=VERIFIER, resource=RESOURCE,
    )
    assert result is not None
    return result


def test_code_exchange_issues_hashed_persistent_refresh_token(repository):
    result = issue_pair(repository)
    assert set(result) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
    assert result["token_type"] == "Bearer" and result["expires_in"] == 3600
    with sqlite3.connect(repository.db_path) as db:
        row = db.execute("SELECT token_hash, user_sub, client_id, scope, resource FROM oauth_refresh_tokens").fetchone()
        database_bytes = repository.db_path.read_bytes()
    assert row[0] == hashlib.sha256(result["refresh_token"].encode()).hexdigest()
    assert row[1:] == (USER, CLIENT, "forex:read forex:preview", RESOURCE)
    assert result["refresh_token"].encode() not in database_bytes
    assert result["access_token"].encode() not in database_bytes


def test_access_token_uses_configured_ttl(repository, monkeypatch):
    monkeypatch.setattr(settings, "oauth_access_token_ttl_seconds", 1234)
    result = issue_pair(repository)
    with sqlite3.connect(repository.db_path) as db:
        created, expires = db.execute("SELECT created_at, expires_at FROM oauth_access_tokens").fetchone()
    assert result["expires_in"] == 1234
    assert (datetime.fromisoformat(expires) - datetime.fromisoformat(created)).total_seconds() == 1234


def test_access_token_status_reports_exact_rejection_categories(repository):
    unknown, claims = repository.access_token_status("not-issued", RESOURCE)
    assert (unknown, claims) == ("token_record_not_found", None)

    mutations = {
        "token_expired": ("expires_at", "2000-01-01T00:00:00+00:00"),
        "revoked_token": ("revoked_at", "2026-01-01T00:00:00+00:00"),
        "audience_resource_mismatch": ("resource", "https://wrong.example"),
        "subject_missing": ("user_sub", ""),
    }
    for expected, (column, value) in mutations.items():
        result = issue_pair(repository)
        token_hash = hashlib.sha256(result["access_token"].encode()).hexdigest()
        with sqlite3.connect(repository.db_path) as db:
            db.execute(
                f"UPDATE oauth_access_tokens SET {column}=? WHERE token_hash=?",
                (value, token_hash),
            )
        category, claims = repository.access_token_status(result["access_token"], RESOURCE)
        assert (category, claims) == (expected, None)


def test_refresh_rotates_and_preserves_original_user(repository):
    original = issue_pair(repository)
    rotated = repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    )
    assert rotated and rotated["refresh_token"] != original["refresh_token"]
    assert rotated["access_token"] != original["access_token"]
    claims = repository.access_token_claims(rotated["access_token"])
    assert claims["sub"] == USER and claims["client_id"] == CLIENT
    with sqlite3.connect(repository.db_path) as db:
        old = db.execute(
            "SELECT rotated_at, replaced_by_token_hash FROM oauth_refresh_tokens WHERE token_hash=?",
            (hashlib.sha256(original["refresh_token"].encode()).hexdigest(),),
        ).fetchone()
    assert old[0] and old[1] == hashlib.sha256(rotated["refresh_token"].encode()).hexdigest()


def test_old_refresh_token_reuse_is_rejected_and_family_revoked(repository):
    original = issue_pair(repository)
    rotated = repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    )
    assert rotated
    assert repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    ) is None
    assert repository.exchange_refresh_token(
        refresh_token=rotated["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    ) is None


def test_concurrent_refresh_has_only_one_success(repository):
    original = issue_pair(repository)

    def refresh(_):
        local = OAuthRepository(repository.db_path, secret="oauth-test-secret")
        return local.exchange_refresh_token(
            refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(refresh, (1, 2)))
    assert sum(result is not None for result in results) == 1


def test_expired_refresh_token_is_invalid(repository):
    original = issue_pair(repository)
    with sqlite3.connect(repository.db_path) as db:
        db.execute("UPDATE oauth_refresh_tokens SET expires_at='2000-01-01T00:00:00+00:00'")
    assert repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    ) is None


def test_revoked_refresh_token_is_invalid(repository):
    original = issue_pair(repository)
    assert repository.revoke_refresh_token(original["refresh_token"])
    assert repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    ) is None


@pytest.mark.parametrize(
    "client,resource", [("wrong-client", RESOURCE), (CLIENT, "https://wrong.example")]
)
def test_refresh_binding_mismatch_is_invalid(repository, client, resource):
    original = issue_pair(repository)
    assert repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=client, resource=resource,
    ) is None


def test_scope_escalation_is_rejected_but_narrowing_is_allowed(repository):
    original = issue_pair(repository)
    assert repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
        scope="forex:read forex:preview forex:execute",
    ) is None
    narrowed = repository.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
        scope="forex:read",
    )
    assert narrowed and narrowed["scope"] == "forex:read"


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "wrong-client"},
        {"resource": "https://wrong.example"},
        {"scope": "forex:read forex:preview forex:execute"},
    ],
)
def test_refresh_endpoint_returns_exact_invalid_grant_for_binding_or_scope_errors(repository, overrides):
    original = issue_pair(repository)
    form = {
        "grant_type": "refresh_token", "refresh_token": original["refresh_token"],
        "client_id": CLIENT, "resource": RESOURCE, **overrides,
    }
    with TestClient(app) as client:
        response = client.post("/oauth/token", data=form)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_grant"}
    assert response.headers["cache-control"] == "no-store"


def test_refresh_records_survive_repository_reinitialization(repository):
    original = issue_pair(repository)
    restarted = OAuthRepository(repository.db_path, secret="oauth-test-secret")
    refreshed = restarted.exchange_refresh_token(
        refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE,
    )
    assert refreshed and restarted.access_token_claims(refreshed["access_token"])["sub"] == USER


def test_existing_oauth_database_is_migrated_without_deletion(tmp_path):
    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE oauth_access_tokens(
            token_hash TEXT PRIMARY KEY, user_sub TEXT NOT NULL, client_id TEXT NOT NULL,
            scope TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO oauth_access_tokens VALUES (?,?,?,?,?,?)",
            ("existing-hash", USER, CLIENT, "forex:read", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"),
        )
    OAuthRepository(db_path, secret="migration-secret")
    with sqlite3.connect(db_path) as db:
        old = db.execute("SELECT user_sub FROM oauth_access_tokens WHERE token_hash='existing-hash'").fetchone()
        refresh_table = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oauth_refresh_tokens'").fetchone()
        columns = {row[1] for row in db.execute("PRAGMA table_info(oauth_access_tokens)")}
    assert old == (USER,)
    assert refresh_table == ("oauth_refresh_tokens",)
    assert "resource" in columns
    assert "revoked_at" in columns


def test_oauth_lifecycle_logs_are_sanitized(repository, caplog):
    original = issue_pair(repository)
    caplog.set_level("INFO", logger="app.api.routes.oauth")
    with TestClient(app) as client:
        response = client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": original["refresh_token"],
            "client_id": CLIENT,
        })
    assert response.status_code == 200
    rotated = response.json()
    assert "grant_type=refresh_token" in caplog.text
    assert "refresh_token_issued=True" in caplog.text
    for secret in (original["refresh_token"], rotated["refresh_token"], rotated["access_token"]):
        assert secret not in caplog.text


def test_oauth_refresh_does_not_mutate_broker_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "shared.db"
    monkeypatch.setattr(settings, "oauth_access_token_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "oauth_refresh_token_ttl_seconds", 7776000)
    brokers = BrokerRepository(db_path, secret="broker-secret")
    brokers.save_connection(USER, base_url="https://demo.tradelocker.com/backend-api", username="broker-user", password="broker-password", server="demo", environment="demo")
    brokers.select_account(USER, "account-1", "2")
    before = brokers.get_connection(USER)
    oauth = OAuthRepository(db_path, secret="oauth-secret")
    original = issue_pair(oauth)
    assert oauth.exchange_refresh_token(refresh_token=original["refresh_token"], client_id=CLIENT, resource=RESOURCE)
    after = brokers.get_connection(USER)
    assert after == before
