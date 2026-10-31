"""Billing webhook logic: signature check, idempotency and plan changes."""
import hashlib
import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing import PaymentEvent
from app.models.user import User
from app.services.usage import PLAN_LIMITS

PROVIDER = "razorpay"
PAID_PLANS = {plan for plan in PLAN_LIMITS if plan != "free"}
UPGRADE_EVENTS = {"subscription.activated", "subscription.charged"}
DOWNGRADE_EVENTS = {"subscription.cancelled", "subscription.halted", "subscription.completed"}


def verify_razorpay_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Razorpay signs the raw request body with HMAC-SHA256 (hex)."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _notes(payload) -> dict:
    try:
        notes = payload["payload"]["subscription"]["entity"].get("notes")
    except (KeyError, TypeError, AttributeError):
        return {}
    return notes if isinstance(notes, dict) else {}


def _to_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def apply_event(db: Session, event_id: str, payload) -> str:
    """Apply one webhook event once. Returns upgraded/downgraded/ignored/user_not_found/duplicate."""
    kind = payload.get("event") if isinstance(payload, dict) else None
    kind = kind if isinstance(kind, str) else ""
    notes = _notes(payload)
    user_id = _to_int(notes.get("user_id"))
    plan = notes.get("plan")
    outcome = "ignored"
    user = None

    try:
        if kind in (UPGRADE_EVENTS | DOWNGRADE_EVENTS) and user_id is not None:
            user = db.get(User, user_id)
            if user is None:
                outcome = "user_not_found"
            elif kind in UPGRADE_EVENTS and plan in PAID_PLANS:
                user.plan = plan
                outcome = "upgraded"
            elif kind in DOWNGRADE_EVENTS and plan in PAID_PLANS and user.plan == plan:
                user.plan = "free"
                outcome = "downgraded"

        # Same transaction as the plan change: a duplicate event rolls both back.
        db.add(PaymentEvent(
            provider=PROVIDER,
            event_id=event_id[:100],
            event_type=kind[:60],
            user_id=user.id if user is not None else None,
            outcome=outcome,
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        return "duplicate"
    except Exception:
        db.rollback()
        raise

    return outcome
