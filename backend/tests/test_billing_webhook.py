import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db
from app.models.user import User
from app.models.revoked_token import RevokedToken  # noqa: F401
from app.models.usage import MonthlyUsage, AIUsageEvent  # noqa: F401
from app.models.billing import PaymentEvent  # noqa: F401

SECRET = "whsec-test-secret"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    with Session() as s:
        user = User(email="payer@example.com", password_hash="x", plan="free", is_active=True)
        s.add(user)
        s.commit()
        user_id = user.id

    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), Session, user_id
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def make_event(kind, user_id, plan):
    return {
        "event": kind,
        "payload": {"subscription": {"entity": {
            "id": "sub_1", "notes": {"user_id": str(user_id), "plan": plan},
        }}},
    }


def sign(body: bytes, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post(client, payload, event_id="evt_1", signature=None, body=None):
    body = body if body is not None else json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
        "X-Razorpay-Signature": signature if signature is not None else sign(body),
    }
    return client.post("/billing/webhook", content=body, headers=headers)


def plan_of(Session, user_id):
    with Session() as s:
        return s.get(User, user_id).plan


def test_webhook_fails_closed_without_secret(env, monkeypatch):
    client, _, user_id = env
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "")
    assert post(client, make_event("subscription.activated", user_id, "pro")).status_code == 503


def test_invalid_signature_is_rejected(env):
    client, Session, user_id = env
    r = post(client, make_event("subscription.activated", user_id, "pro"), signature="deadbeef")
    assert r.status_code == 400
    assert plan_of(Session, user_id) == "free"


def test_tampered_body_is_rejected(env):
    client, Session, user_id = env
    original = json.dumps(make_event("subscription.activated", user_id, "pro")).encode()
    tampered = json.dumps(make_event("subscription.activated", user_id, "enterprise")).encode()
    r = post(client, None, body=tampered, signature=sign(original))
    assert r.status_code == 400
    assert plan_of(Session, user_id) == "free"


def test_valid_activation_upgrades_user(env):
    client, Session, user_id = env
    r = post(client, make_event("subscription.activated", user_id, "pro"))
    assert r.status_code == 200
    assert r.json()["status"] == "upgraded"
    assert plan_of(Session, user_id) == "pro"


def test_duplicate_event_is_not_applied_twice(env):
    client, Session, user_id = env
    event = make_event("subscription.activated", user_id, "pro")
    assert post(client, event, event_id="evt_dup").json()["status"] == "upgraded"

    with Session() as s:  # simulate an admin changing the plan afterwards
        s.get(User, user_id).plan = "free"
        s.commit()

    r = post(client, event, event_id="evt_dup")
    assert r.status_code == 200
    assert r.json()["status"] == "duplicate"
    assert plan_of(Session, user_id) == "free"


def test_cancellation_downgrades_user(env):
    client, Session, user_id = env
    post(client, make_event("subscription.activated", user_id, "pro"), event_id="evt_a")
    r = post(client, make_event("subscription.cancelled", user_id, "pro"), event_id="evt_b")
    assert r.json()["status"] == "downgraded"
    assert plan_of(Session, user_id) == "free"


def test_stale_cancellation_does_not_downgrade_newer_plan(env):
    client, Session, user_id = env
    post(client, make_event("subscription.activated", user_id, "enterprise"), event_id="evt_c")
    r = post(client, make_event("subscription.cancelled", user_id, "pro"), event_id="evt_d")
    assert r.json()["status"] == "ignored"
    assert plan_of(Session, user_id) == "enterprise"


def test_unknown_plan_is_ignored(env):
    client, Session, user_id = env
    r = post(client, make_event("subscription.activated", user_id, "superplan"))
    assert r.json()["status"] == "ignored"
    assert plan_of(Session, user_id) == "free"


def test_unknown_user_is_reported_not_crashed(env):
    client, _, _ = env
    r = post(client, make_event("subscription.activated", 987654, "pro"))
    assert r.status_code == 200
    assert r.json()["status"] == "user_not_found"
