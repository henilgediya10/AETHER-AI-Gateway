import time
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.routes.auth import get_current_user
from app.services import security
from app.models.user import User  # noqa: F401
from app.models.revoked_token import RevokedToken  # noqa: F401
from app.models.usage import MonthlyUsage, AIUsageEvent  # noqa: F401

EMAIL = "jwt-test@example.com"
PASSWORD = "CorrectHorse-123"


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _login(client):
    r = client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 201, r.text
    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}



def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_export_returns_own_data_without_password(client):
    token = _login(client)
    r = client.get("/account/export", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["account"]["email"] == EMAIL.lower()
    assert "password_hash" not in r.text
    assert PASSWORD not in r.text


def test_export_requires_login(client):
    r = client.get("/account/export")
    assert r.status_code in (401, 403)


def test_export_shows_only_my_own_data(client):
    _login(client)
    other = "other.person@example.com"
    client.post("/auth/register", json={"email": other, "password": PASSWORD})
    r = client.post("/auth/login", json={"email": other, "password": PASSWORD})
    assert r.status_code == 200
    r2 = client.get("/account/export", headers=_auth(r.json()["access_token"]))
    assert r2.json()["account"]["email"] == other


def test_delete_requires_login(client):
    r = client.request("DELETE", "/account", json={"password": PASSWORD})
    assert r.status_code in (401, 403)


def test_delete_without_password_is_rejected(client):
    token = _login(client)
    r = client.request("DELETE", "/account", json={}, headers=_auth(token))
    assert r.status_code == 422


def test_delete_with_wrong_password_keeps_account(client):
    token = _login(client)
    r = client.request("DELETE", "/account", json={"password": "wrong-password-123"}, headers=_auth(token))
    assert r.status_code == 403
    assert client.get("/account/export", headers=_auth(token)).status_code == 200


def test_delete_with_right_password_removes_account(client):
    token = _login(client)
    r = client.request("DELETE", "/account", json={"password": PASSWORD}, headers=_auth(token))
    assert r.status_code == 200
    # the old token no longer works
    assert client.get("/account/export", headers=_auth(token)).status_code == 401
    # the user can no longer log in
    again = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert again.status_code != 200
