"""Authentication integration tests; SQLite database and fake provider, no real API keys."""
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.routes.auth import get_current_user
from app.services.security import create_access_token, decode_access_token


def test_jwt_unique_for_separate_logins():
    first = create_access_token(101)
    second = create_access_token(101)
    assert first != second
    assert decode_access_token(first)["jti"] != decode_access_token(second)["jti"]


def test_chat_requires_authentication():
    app.dependency_overrides.pop(get_current_user, None)
    result = TestClient(app).post("/chat", json={"message": "Hello"})
    assert result.status_code == 401


def test_auth_routes_exist():
    paths = set(TestClient(app).get("/openapi.json").json()["paths"])
    assert {"/auth/register", "/auth/login", "/auth/me", "/auth/logout"} <= paths
