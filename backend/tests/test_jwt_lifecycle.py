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


def test_valid_token_reaches_me(client):
    token = _login(client)
    r = client.get("/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["email"] == EMAIL


def test_missing_token_is_rejected(client):
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_token(client):
    token = _login(client)
    assert client.post("/auth/logout", headers=_auth(token)).status_code == 200
    # Same token must no longer work anywhere.
    assert client.get("/auth/me", headers=_auth(token)).status_code == 401


def test_second_logout_with_revoked_token_is_rejected(client):
    token = _login(client)
    assert client.post("/auth/logout", headers=_auth(token)).status_code == 200
    r = client.post("/auth/logout", headers=_auth(token))
    assert r.status_code in (401, 409)


def test_expired_token_is_rejected(client):
    _login(client)
    now = int(time.time())
    expired = jwt.encode(
        {"sub": "1", "jti": "expired-test", "iat": now - 7200, "exp": now - 3600},
        security.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    assert client.get("/auth/me", headers=_auth(expired)).status_code == 401


def test_tampered_token_is_rejected(client):
    token = _login(client)
    head, payload, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert client.get("/auth/me", headers=_auth(f"{head}.{payload}.{flipped}")).status_code == 401


def test_token_signed_with_wrong_key_is_rejected(client):
    _login(client)
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "1", "jti": "forged", "iat": now, "exp": now + 3600},
        "x" * 48,
        algorithm=security.ALGORITHM,
    )
    assert client.get("/auth/me", headers=_auth(forged)).status_code == 401


def test_wrong_password_login_fails(client):
    client.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
    r = client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password-1"})
    assert r.status_code == 401
