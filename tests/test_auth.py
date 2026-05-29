"""P1-T5 Auth API + multi-tenant isolation tests.

Uses SQLite in-memory DB to avoid requiring PostgreSQL.
"""

import pytest
from unittest.mock import patch
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.pool import StaticPool

from src.api.chat import app
from src.db.models import User
from src.db import engine as engine_module


# === Fixtures ===

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

    # Patch the engine module so get_session() uses our test DB
    monkeypatch.setattr(engine_module, "get_engine", lambda: test_engine)
    monkeypatch.setattr(engine_module, "get_session", _get_session)

    # Also patch the auth dependencies to use our test session
    from src.auth import dependencies as auth_deps
    monkeypatch.setattr(auth_deps, "_get_db", lambda: iter([_get_session()]))

    yield

    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(app)


# === Register ===

def test_register_success(client):
    resp = client.post("/api/auth/register", json={
        "username": "alice",
        "password": "secret123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_username(client):
    client.post("/api/auth/register", json={"username": "bob", "password": "pass1"})
    resp = client.post("/api/auth/register", json={"username": "bob", "password": "pass2"})
    assert resp.status_code == 409


# === Login ===

def test_login_success(client):
    client.post("/api/auth/register", json={"username": "carol", "password": "mypass"})
    resp = client.post("/api/auth/login", json={"username": "carol", "password": "mypass"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(client):
    client.post("/api/auth/register", json={"username": "dave", "password": "correct"})
    resp = client.post("/api/auth/login", json={"username": "dave", "password": "wrong"})
    assert resp.status_code == 401


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401


# === Me (authenticated endpoint) ===

def test_me_authenticated(client):
    reg = client.post("/api/auth/register", json={"username": "eve", "password": "pass"})
    token = reg.json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "eve"
    assert "tenant_id" in data


def test_me_no_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_invalid_token(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
    assert resp.status_code == 401


# === Refresh ===

def test_refresh_success(client):
    reg = client.post("/api/auth/register", json={"username": "frank", "password": "pass"})
    refresh_token = reg.json()["refresh_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_refresh_with_access_token_fails(client):
    """Using an access token as refresh token should fail."""
    reg = client.post("/api/auth/register", json={"username": "grace", "password": "pass"})
    access_token = reg.json()["access_token"]
    resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


# === Tenant isolation ===

def test_different_users_get_different_tenants(client):
    """Each registered user gets a unique tenant_id."""
    r1 = client.post("/api/auth/register", json={"username": "tenant_a", "password": "pass"})
    r2 = client.post("/api/auth/register", json={"username": "tenant_b", "password": "pass"})

    t1 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r1.json()['access_token']}"})
    t2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r2.json()['access_token']}"})

    assert t1.json()["tenant_id"] != t2.json()["tenant_id"]
