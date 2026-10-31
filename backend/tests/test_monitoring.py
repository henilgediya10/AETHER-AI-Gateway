import logging
import pytest
from fastapi.testclient import TestClient
from app.main import app, ProviderMetrics


def test_metrics_reject_missing_token(monkeypatch):
    monkeypatch.setenv("AETHER_METRICS_TOKEN", "test-monitoring-secret")
    client = TestClient(app)
    response = client.get("/admin/metrics")
    assert response.status_code == 403


def test_metrics_reject_wrong_token(monkeypatch):
    monkeypatch.setenv("AETHER_METRICS_TOKEN", "test-monitoring-secret")
    client = TestClient(app)
    response = client.get(
        "/admin/metrics",
        headers={"X-Metrics-Token": "wrong-token"},
    )
    assert response.status_code == 403


def test_metrics_accept_valid_token(monkeypatch):
    monkeypatch.setenv("AETHER_METRICS_TOKEN", "test-monitoring-secret")
    client = TestClient(app)
    response = client.get(
        "/admin/metrics",
        headers={"X-Metrics-Token": "test-monitoring-secret"},
    )
    assert response.status_code == 200
    assert "providers" in response.json()


def test_provider_success_metrics():
    metrics = ProviderMetrics()

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider success",
        args=(),
        exc_info=None,
    )
    record.event = "provider_call_success"
    record.provider = "gemini"
    record.latency_ms = 200
    record.tokens_total = 150

    metrics.emit(record)
    result = metrics.snapshot()["providers"]["gemini"]

    assert result["successful_attempts"] == 1
    assert result["average_latency_ms"] == 200
    assert result["tokens_total"] == 150


def test_provider_failure_metrics():
    metrics = ProviderMetrics()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failure",
        args=(),
        exc_info=None,
    )
    record.event = "provider_call_failure"
    record.provider = "gemini"
    record.latency_ms = 500
    record.status = 503

    metrics.emit(record)
    result = metrics.snapshot()["providers"]["gemini"]

    assert result["failed_attempts"] == 1
    assert result["failures_by_status"]["503"] == 1
