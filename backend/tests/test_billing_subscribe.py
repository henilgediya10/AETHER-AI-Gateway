from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import billing as billing_routes
from app.routes.billing import get_current_user
from app.services import razorpay_client
from app.services.razorpay_client import BillingConfigError, RazorpayError


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def logged_in_free_user():
    fake_user = SimpleNamespace(id=42, plan="free", email="a@b.com")
    app.dependency_overrides[get_current_user] = lambda: fake_user
    yield fake_user
    app.dependency_overrides.clear()


def test_subscribe_success_uses_id_from_token(client, logged_in_free_user, monkeypatch):
    seen = {}

    def fake_create(*, user_id, plan, total_count=12):
        seen["user_id"], seen["plan"] = user_id, plan
        return {"subscription_id": "sub_123", "payment_url": "https://rzp.io/x"}

    monkeypatch.setattr(billing_routes, "create_subscription", fake_create)

    # We try to sneak in a different user_id; it must be ignored
    r = client.post("/billing/subscribe", json={"plan": "pro", "user_id": 999})

    assert r.status_code == 200
    assert r.json()["subscription_id"] == "sub_123"
    assert seen == {"user_id": 42, "plan": "pro"}


def test_subscribe_rejects_free_plan(client, logged_in_free_user):
    r = client.post("/billing/subscribe", json={"plan": "free"})
    assert r.status_code == 422


def test_subscribe_rejects_unknown_plan(client, logged_in_free_user):
    r = client.post("/billing/subscribe", json={"plan": "gold"})
    assert r.status_code == 422


def test_subscribe_requires_login(client):
    app.dependency_overrides.clear()
    r = client.post("/billing/subscribe", json={"plan": "pro"})
    assert r.status_code in (401, 403)


def test_subscribe_already_on_plan(client, logged_in_free_user):
    logged_in_free_user.plan = "pro"
    r = client.post("/billing/subscribe", json={"plan": "pro"})
    assert r.status_code == 409


def test_subscribe_razorpay_down_gives_502(client, logged_in_free_user, monkeypatch):
    def boom(**kwargs):
        raise RazorpayError("down")

    monkeypatch.setattr(billing_routes, "create_subscription", boom)
    r = client.post("/billing/subscribe", json={"plan": "pro"})
    assert r.status_code == 502


def test_subscribe_missing_config_gives_503(client, logged_in_free_user, monkeypatch):
    def boom(**kwargs):
        raise BillingConfigError("missing")

    monkeypatch.setattr(billing_routes, "create_subscription", boom)
    r = client.post("/billing/subscribe", json={"plan": "pro"})
    assert r.status_code == 503


def test_payload_sent_to_razorpay_has_notes(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("RAZORPAY_PLAN_PRO", "plan_abc")
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "sub_1", "short_url": "https://rzp.io/y"}

    def fake_post(url, json, auth, timeout):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(razorpay_client.httpx, "post", fake_post)

    result = razorpay_client.create_subscription(user_id=7, plan="pro")

    assert result["subscription_id"] == "sub_1"
    assert captured["json"]["plan_id"] == "plan_abc"
    assert captured["json"]["notes"] == {"user_id": "7", "plan": "pro"}
