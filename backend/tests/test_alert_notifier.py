import pytest

from app.services import alert_notifier
from app.services.alert_notifier import SlackError, format_alert, notify_alerts

ERROR_ALERT = {"provider": "gemini", "type": "high_error_rate",
               "severity": "critical", "value": 35.5, "threshold": 20}
LATENCY_ALERT = {"provider": "gemini", "type": "high_latency",
                 "severity": "warning", "value": 12000, "threshold": 10000}


class FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, key):
        self.store.pop(key, None)


class DownRedis:
    def set(self, *args, **kwargs):
        raise RuntimeError("redis is down")

    def delete(self, *args, **kwargs):
        raise RuntimeError("redis is down")


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture
def slack_calls(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json})
        return FakeResponse(200)

    monkeypatch.setattr(alert_notifier.httpx, "post", fake_post)
    return calls


def test_format_error_rate():
    text = format_alert(ERROR_ALERT)
    assert "CRITICAL" in text and "gemini" in text and "35.5%" in text


def test_format_latency():
    text = format_alert(LATENCY_ALERT)
    assert "WARNING" in text and "12000 ms" in text


def test_empty_url_sends_nothing(slack_calls):
    redis_client = FakeRedis()
    result = notify_alerts([ERROR_ALERT], redis_client=redis_client, webhook_url="")
    assert result == {"sent": 0, "skipped": 1, "failed": 0}
    assert slack_calls == []
    assert redis_client.store == {}  # must not use up the cooldown


def test_sends_once_then_skips_repeat(slack_calls):
    redis_client = FakeRedis()
    first = notify_alerts([ERROR_ALERT], redis_client=redis_client, webhook_url="https://hooks.test/x")
    second = notify_alerts([ERROR_ALERT], redis_client=redis_client, webhook_url="https://hooks.test/x")
    assert first["sent"] == 1
    assert second == {"sent": 0, "skipped": 1, "failed": 0}
    assert len(slack_calls) == 1


def test_different_alerts_are_both_sent(slack_calls):
    result = notify_alerts([ERROR_ALERT, LATENCY_ALERT], redis_client=FakeRedis(),
                           webhook_url="https://hooks.test/x")
    assert result["sent"] == 2
    assert len(slack_calls) == 2


def test_slack_failure_releases_cooldown(monkeypatch):
    redis_client = FakeRedis()

    def bad_post(url, json, timeout):
        return FakeResponse(500)

    monkeypatch.setattr(alert_notifier.httpx, "post", bad_post)
    result = notify_alerts([ERROR_ALERT], redis_client=redis_client, webhook_url="https://hooks.test/x")
    assert result["failed"] == 1
    assert redis_client.store == {}  # so the next run can retry


def test_redis_down_still_sends(slack_calls):
    result = notify_alerts([ERROR_ALERT], redis_client=DownRedis(), webhook_url="https://hooks.test/x")
    assert result["sent"] == 1


def test_send_slack_raises_on_error(monkeypatch):
    monkeypatch.setattr(alert_notifier.httpx, "post", lambda url, json, timeout: FakeResponse(404))
    with pytest.raises(SlackError):
        alert_notifier.send_slack("https://hooks.test/x", "hi")
