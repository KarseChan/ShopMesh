"""Tests for API Key management (P3-T3).

Tests cover:
- Key generation / hashing / verification
- CRUD endpoints (create / list / revoke)
- X-API-Key header auth as alternative to JWT
- Tenant isolation

Uses SQLite in-memory DB to avoid requiring PostgreSQL.
"""

import pytest
from unittest.mock import patch
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from src.api.chat import app
from src.db.models import ApiKey, User
from src.db import engine as engine_module
from src.auth.apikey import (
    generate_api_key,
    hash_key,
    verify_key,
    create_api_key,
    list_api_keys,
    revoke_api_key,
    validate_api_key,
)
from src.auth.jwt import create_access_token, hash_password


# ---------- Fixtures ----------

@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    """Override DB engine with SQLite in-memory for all tests."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    def _get_session():
        return Session(test_engine)

    monkeypatch.setattr(engine_module, "get_engine", lambda: test_engine)
    monkeypatch.setattr(engine_module, "get_session", _get_session)

    from src.auth import dependencies as auth_deps
    monkeypatch.setattr(auth_deps, "_get_db", lambda: iter([_get_session()]))

    yield

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(app)


def _create_test_user(tenant_id: str = "t1", username: str = "testuser") -> User:
    """Create a test user in the DB."""
    with engine_module.get_session() as db:
        user = User(
            user_id=f"uid_{username}",
            username=username,
            hashed_password=hash_password("pass123"),
            tenant_id=tenant_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _auth_header(user: User) -> dict:
    """Create a JWT auth header for a user."""
    token = create_access_token(user.user_id, user.tenant_id)
    return {"Authorization": f"Bearer {token}"}


# ---------- Unit tests: key generation ----------

class TestKeyGeneration:
    def test_generate_api_key_format(self):
        full_key, key_id = generate_api_key()
        assert full_key.startswith("sk_live_")
        assert key_id.startswith("sk_live_")
        assert len(full_key) > len(key_id)

    def test_generate_api_key_unique(self):
        keys = {generate_api_key()[0] for _ in range(100)}
        assert len(keys) == 100  # all unique

    def test_hash_and_verify(self):
        full_key, _ = generate_api_key()
        hashed = hash_key(full_key)
        assert verify_key(full_key, hashed) is True
        assert verify_key("wrong_key", hashed) is False

    def test_key_id_is_prefix(self):
        full_key, key_id = generate_api_key()
        assert full_key.startswith(key_id)


# ---------- Unit tests: DB operations ----------

class TestApiKeyDB:
    def test_create_and_list(self):
        with engine_module.get_session() as db:
            full_key, api_key = create_api_key(db, tenant_id="t1", name="Test Key")
            assert api_key.key_id.startswith("sk_live_")
            assert api_key.tenant_id == "t1"
            assert api_key.name == "Test Key"
            assert api_key.is_active is True

            keys = list_api_keys(db, tenant_id="t1")
            assert len(keys) == 1
            assert keys[0].key_id == api_key.key_id

    def test_revoke(self):
        with engine_module.get_session() as db:
            full_key, api_key = create_api_key(db, tenant_id="t1")
            assert revoke_api_key(db, api_key.key_id, "t1") is True

            keys = list_api_keys(db, tenant_id="t1")
            assert len(keys) == 0  # revoked keys excluded

    def test_revoke_wrong_tenant(self):
        with engine_module.get_session() as db:
            full_key, api_key = create_api_key(db, tenant_id="t1")
            assert revoke_api_key(db, api_key.key_id, "t2") is False

    def test_validate_api_key(self):
        with engine_module.get_session() as db:
            full_key, api_key = create_api_key(db, tenant_id="t1")
            result = validate_api_key(db, full_key)
            assert result is not None
            assert result.key_id == api_key.key_id

    def test_validate_wrong_key(self):
        with engine_module.get_session() as db:
            create_api_key(db, tenant_id="t1")
            result = validate_api_key(db, "sk_live_wrong_key_here")
            assert result is None

    def test_validate_revoked_key(self):
        with engine_module.get_session() as db:
            full_key, api_key = create_api_key(db, tenant_id="t1")
            revoke_api_key(db, api_key.key_id, "t1")
            result = validate_api_key(db, full_key)
            assert result is None


# ---------- API tests: CRUD ----------

class TestApiKeyAPI:
    def test_create_key(self, client):
        user = _create_test_user()
        resp = client.post(
            "/api/auth/api-keys",
            json={"name": "My App"},
            headers=_auth_header(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["api_key"].startswith("sk_live_")
        assert data["name"] == "My App"
        assert "key_id" in data

    def test_list_keys(self, client):
        user = _create_test_user()
        client.post("/api/auth/api-keys", json={"name": "Key 1"}, headers=_auth_header(user))
        client.post("/api/auth/api-keys", json={"name": "Key 2"}, headers=_auth_header(user))

        resp = client.get("/api/auth/api-keys", headers=_auth_header(user))
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 2

    def test_revoke_key(self, client):
        user = _create_test_user()
        create_resp = client.post("/api/auth/api-keys", json={"name": "To Revoke"}, headers=_auth_header(user))
        key_id = create_resp.json()["key_id"]

        resp = client.delete(f"/api/auth/api-keys/{key_id}", headers=_auth_header(user))
        assert resp.status_code == 204

        list_resp = client.get("/api/auth/api-keys", headers=_auth_header(user))
        assert len(list_resp.json()) == 0

    def test_create_requires_auth(self, client):
        resp = client.post("/api/auth/api-keys", json={"name": "No Auth"})
        assert resp.status_code == 401

    def test_revoke_nonexistent(self, client):
        user = _create_test_user()
        resp = client.delete("/api/auth/api-keys/sk_live_nonexistent", headers=_auth_header(user))
        assert resp.status_code == 404


# ---------- API tests: X-API-Key auth ----------

class TestApiKeyAuth:
    def test_api_key_auth_header(self, client):
        """X-API-Key header should authenticate requests."""
        user = _create_test_user(tenant_id="merchant1")

        create_resp = client.post(
            "/api/auth/api-keys",
            json={"name": "Integration"},
            headers=_auth_header(user),
        )
        full_key = create_resp.json()["api_key"]

        # Use API key to access a protected endpoint
        resp = client.get("/api/auth/me", headers={"X-API-Key": full_key})
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "merchant1"

    def test_invalid_api_key_rejected(self, client):
        resp = client.get("/api/auth/me", headers={"X-API-Key": "sk_live_invalid"})
        assert resp.status_code == 401

    def test_missing_both_auth_methods(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401


# ---------- Tenant isolation ----------

class TestTenantIsolation:
    def test_keys_isolated_by_tenant(self, client):
        user1 = _create_test_user(tenant_id="t1", username="u1")
        user2 = _create_test_user(tenant_id="t2", username="u2")

        client.post("/api/auth/api-keys", json={"name": "T1 Key"}, headers=_auth_header(user1))
        client.post("/api/auth/api-keys", json={"name": "T2 Key"}, headers=_auth_header(user2))

        resp1 = client.get("/api/auth/api-keys", headers=_auth_header(user1))
        assert len(resp1.json()) == 1
        assert resp1.json()[0]["name"] == "T1 Key"

        resp2 = client.get("/api/auth/api-keys", headers=_auth_header(user2))
        assert len(resp2.json()) == 1
        assert resp2.json()[0]["name"] == "T2 Key"

    def test_cannot_revoke_other_tenant_key(self, client):
        user1 = _create_test_user(tenant_id="t1", username="u1")
        user2 = _create_test_user(tenant_id="t2", username="u2")

        create_resp = client.post("/api/auth/api-keys", json={"name": "T1 Key"}, headers=_auth_header(user1))
        key_id = create_resp.json()["key_id"]

        resp = client.delete(f"/api/auth/api-keys/{key_id}", headers=_auth_header(user2))
        assert resp.status_code == 404
