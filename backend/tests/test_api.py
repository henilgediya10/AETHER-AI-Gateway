import os
os.environ["USE_QWEN"] = "false"

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import (
    app,
    ProviderError,
    COOLDOWN,
    CACHE,
    compact,
)
from app.routes.auth import get_current_user
from app.router import classify, select_provider

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_chat_auth():
    """
    Mock authentication and clear shared state only for this test file.
    Real application security remains unchanged.
    """
    fake_user = SimpleNamespace(
        id=999,
        plan="free",
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = lambda: fake_user

    # Clear shared state between tests
    COOLDOWN.clear()
    CACHE.clear()

    # Reset rate-limiter counters if the module exists
    try:
        from app.services.rate_limiter import _requests
        _requests.clear()
    except ImportError:
        pass

    with (
        patch("app.main.reserve_request", return_value=(1, 50)),
        patch("app.main.record_success"),
    ):
        yield

    # Cleanup
    app.dependency_overrides.pop(get_current_user, None)
    COOLDOWN.clear()
    CACHE.clear()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_no_keys():
    response = client.get("/providers/status")
    assert response.status_code == 200
    assert "gemini" in response.json()


def test_models():
    response = client.get("/models")
    assert response.status_code == 200
    assert "gemini" in response.json()


def test_classification():
    assert classify("debug my python code") == "coding"


def test_routing():
    assert select_provider("coding", "auto", ["gemini", "openai"]) == "openai"


def test_context():
    messages = compact([], "hello")
    assert messages[-1]["content"] == "hello"


def test_missing_keys(monkeypatch):
    for key in [
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "provider": "openai",
        },
    )

    assert response.status_code == 503
    failures = response.json()["detail"]["failures"]
    assert failures[0]["reason"] == "not configured"


def test_gemini_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-only")

    result = {
        "answer": "Hello",
        "model": "gemini-test",
        "tokens": {
            "input": 1,
            "output": 1,
            "total": 2,
        },
    }

    with patch(
        "app.main.call_provider",
        new_callable=AsyncMock,
        return_value=result,
    ) as mocked_provider:
        response = client.post(
            "/chat",
            json={
                "message": "python code",
                "provider": "openai",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "gemini"
    assert data["fallback"] is True
    assert data["reply"] == "Hello"
    assert mocked_provider.await_count == 1


def test_provider_error_404(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-only")

    with patch(
        "app.main.call_provider",
        new_callable=AsyncMock,
        side_effect=ProviderError(
            "gemini",
            404,
            "model missing",
        ),
    ):
        response = client.post(
            "/chat",
            json={
                "message": "hello",
                "provider": "gemini",
            },
        )

    assert response.status_code == 503
    failures = response.json()["detail"]["failures"]
    assert failures[0]["status"] == 404
