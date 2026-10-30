
import os
os.environ["USE_QWEN"] = "false"

import asyncio
import httpx
import pytest

from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import main
from app.routes.auth import get_current_user


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Reset provider state and use fake API keys for testing."""

    main.COOLDOWN.clear()
    main.CACHE.clear()

    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE_TEST_KEY")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Clear rate-limit counters between tests.
    try:
        from app.services.rate_limiter import _requests
        _requests.clear()
    except ImportError:
        if hasattr(main, "_request_history"):
            main._request_history.clear()

    yield

    main.COOLDOWN.clear()
    main.CACHE.clear()


@pytest.mark.parametrize(
    "status",
    [400, 401, 403, 404, 429, 500, 502, 503],
)
def test_gemini_http_errors(status):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status,
            json={"error": {"message": "test error"}},
        )
    )

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(main.ProviderError) as exc:
                await main.call_provider(
                    "gemini",
                    [{"role": "user", "content": "hi"}],
                    client,
                )

            assert exc.value.status == status

    asyncio.run(go())


def test_gemini_success_and_usage():
    def handler(req):
        assert req.headers["x-goog-api-key"] == "FAKE_TEST_KEY"
        assert req.url.path.endswith(":generateContent")

        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "It works"}]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 5,
                },
            },
        )

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            result = await main.call_provider(
                "gemini",
                [{"role": "user", "content": "hi"}],
                client,
            )

            assert result["answer"] == "It works"
            assert result["tokens"]["total"] == 5

    asyncio.run(go())


def test_timeout():
    def handler(req):
        raise httpx.ConnectTimeout("timed out")

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(main.ProviderError) as exc:
                await main.call_provider(
                    "gemini",
                    [{"role": "user", "content": "hi"}],
                    client,
                )

            assert exc.value.status == 503

    asyncio.run(go())


def test_cooldown_no_calls():
    main.COOLDOWN["gemini"] = main.time.monotonic() + 30

    async def go():
        with patch(
            "app.main.call_provider",
            new_callable=AsyncMock,
        ) as mocked_provider:

            with pytest.raises(main.ProviderError) as exc:
                await main.attempt(
                    "gemini",
                    [{"role": "user", "content": "hi"}],
                    None,
                )

            assert exc.value.status == 429
            mocked_provider.assert_not_called()

    asyncio.run(go())


def test_fallback_after_other_provider_failure(monkeypatch):
    """OpenAI failure should fall back to Gemini."""

    monkeypatch.setenv("OPENAI_API_KEY", "FAKE_TEST_KEY")

    async def fake_provider(provider, messages, client):
        if provider == "openai":
            raise main.ProviderError(
                "openai",
                503,
                "outage",
            )

        return {
            "answer": "Gemini response",
            "model": "test-model",
            "tokens": {
                "input": 1,
                "output": 2,
                "total": 3,
            },
        }

    fake_user = SimpleNamespace(
        id=999,
        plan="free",
        is_active=True,
    )

    main.app.dependency_overrides[get_current_user] = lambda: fake_user

    try:
        with (
            patch("app.main.call_provider", side_effect=fake_provider),
            patch("app.main.asyncio.sleep", new_callable=AsyncMock),
            patch("app.main.reserve_request", return_value=(1, 50)),
            patch("app.main.record_success"),
        ):
            response = TestClient(main.app).post(
                "/chat",
                json={
                    "message": "write python code",
                    "provider": "openai",
                },
            )

    finally:
        main.app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()["provider"] == "gemini"
    assert response.json()["fallback"] is True
    assert response.json()["reply"] == "Gemini response"


def test_404_no_retry():
    async def go():
        with patch(
            "app.main.call_provider",
            new_callable=AsyncMock,
            side_effect=main.ProviderError(
                "gemini",
                404,
                "not found",
            ),
        ) as mocked_provider:

            with pytest.raises(main.ProviderError):
                await main.attempt("gemini", [], None)

            assert mocked_provider.await_count == 1

    asyncio.run(go())


def test_no_secret_in_zip():
    """Verify that a distributable ZIP excludes .env files."""

    archive = (
        Path(__file__).resolve().parents[3]
        / "AETHER_AI_Gateway_Iteration4.zip"
    )

    if archive.exists():
        with ZipFile(archive) as zf:
            assert not any(
                name.endswith("/.env") or name == ".env"
                for name in zf.namelist()
            )
