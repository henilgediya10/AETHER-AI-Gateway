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



from types import SimpleNamespace

from app.main import app, require_admin


def _register(client, email):
    r = client.post("/auth/register", json={"email": email, "password": PASSWORD})
    assert r.status_code in (200, 201)


def _as_admin():
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=0, email="admin@example.com")


def _clear_admin():
    app.dependency_overrides.pop(require_admin, None)


def test_admin_users_default_is_unchanged(client):
    _register(client, "page.one@example.com")
    _as_admin()
    try:
        r = client.get("/admin/users")
    finally:
        _clear_admin()
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 500 and body["page"] == 1
    assert body["total"] >= 1 and len(body["users"]) >= 1


def test_admin_users_pages_do_not_overlap(client):
    for i in range(3):
        _register(client, f"page.user{i}@example.com")
    _as_admin()
    try:
        first = client.get("/admin/users?page=1&page_size=2").json()
        second = client.get("/admin/users?page=2&page_size=2").json()
    finally:
        _clear_admin()
    assert len(first["users"]) == 2
    assert first["total"] >= 3 and first["pages"] >= 2
    ids1 = {u["id"] for u in first["users"]}
    ids2 = {u["id"] for u in second["users"]}
    assert ids2 and ids1.isdisjoint(ids2)


def test_admin_users_rejects_bad_page_values(client):
    _as_admin()
    try:
        assert client.get("/admin/users?page=0").status_code == 422
        assert client.get("/admin/users?page_size=0").status_code == 422
        assert client.get("/admin/users?page_size=501").status_code == 422
    finally:
        _clear_admin()


def test_admin_users_still_blocks_normal_users(client):
    token = _login(client)
    r = client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
