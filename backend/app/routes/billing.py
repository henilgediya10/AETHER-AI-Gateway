"""POST /billing/webhook - Razorpay subscription events."""
import hashlib
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.billing import apply_event, verify_razorpay_signature

router = APIRouter(prefix="/billing", tags=["Billing"])
log = logging.getLogger("aether")

MAX_BODY_BYTES = 1_000_000


@router.post("/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Fail closed: without a secret nobody can be trusted to change plans.
        raise HTTPException(status_code=503, detail="Billing is not configured")

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_razorpay_signature(raw, signature, secret):
        log.warning("billing_webhook_invalid_signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_id = request.headers.get("X-Razorpay-Event-Id") or hashlib.sha256(raw).hexdigest()
    outcome = await run_in_threadpool(apply_event, db, event_id, payload)
    log.info("billing_webhook_processed", extra={"event": "billing_webhook", "outcome": outcome})

    # Always 200 for verified events (even duplicates/ignored) so Razorpay stops retrying.
    return {"status": outcome}


# ===== Subscribe endpoint (added by setup_billing.ps1) =====
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.services.razorpay_client import (
    BillingConfigError,
    RazorpayError,
    create_subscription,
)
from app.routes.auth import get_current_user


class SubscribeRequest(BaseModel):
    plan: Literal["pro", "enterprise"]  # "free" or anything else gives 422


@router.post("/subscribe")
def subscribe(body: SubscribeRequest, current_user=Depends(get_current_user)):
    if current_user.plan == body.plan:
        raise HTTPException(status_code=409, detail="You are already on this plan")
    try:
        return create_subscription(user_id=current_user.id, plan=body.plan)
    except BillingConfigError:
        raise HTTPException(status_code=503, detail="Billing is not configured")
    except RazorpayError:
        raise HTTPException(status_code=502, detail="Payment provider error")
