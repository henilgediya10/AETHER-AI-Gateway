import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app, log

TOKEN = "test-metrics-token-123"


def _get(token=None):
    headers = {"X-Metrics-Token": token} if token is not None else {}
    with patch.dict(os.environ, {"AETHER_METRICS_TOKEN": TOKEN}):
        return TestClient(app).get("/admin/metrics", headers=headers)


def test_metrics_requires_token():
    assert _get().status_code == 403


def test_metrics_rejects_wrong_token():
    assert _get("wrong-token").status_code == 403


def test_metrics_with_valid_token_includes_alerts():
    r = _get(TOKEN)
    assert r.status_code == 200
    body = r.json()
    assert "providers" in body
    assert isinstance(body["alerts"], list)


def test_high_error_rate_alert_appears():
    for _ in range(12):
        log.warning(
            "provider_call_failure",
            extra={
                "event": "provider_call_failure",
                "provider": "alert-test-provider",
                "status": 500,
                "latency_ms": 100,
            },
        )
    body = _get(TOKEN).json()
    found = [
        a for a in body["alerts"]
        if a["provider"] == "alert-test-provider" and a["type"] == "high_error_rate"
    ]
    assert found and found[0]["severity"] == "critical"
