"""Account routes: export my data, delete my account."""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.billing import PaymentEvent
from app.models.revoked_token import RevokedToken
from app.models.usage import AIUsageEvent, MonthlyUsage
from app.models.user import User
from app.routes.auth import bearer_scheme, decode_access_token, get_current_user
from app.services.security import verify_and_rehash

router = APIRouter(tags=["Account"])


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128, repr=False)


@router.get("/account/export")
def export_my_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # The user comes from the login token, never from the request.
    uid = current_user.id
    monthly = db.scalars(select(MonthlyUsage).where(MonthlyUsage.user_id == uid)).all()
    events = db.scalars(select(AIUsageEvent).where(AIUsageEvent.user_id == uid)).all()
    payments = db.scalars(select(PaymentEvent).where(PaymentEvent.user_id == uid)).all()
    return {
        "account": {
            "id": current_user.id,
            "email": current_user.email,
            "plan": current_user.plan,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
        },
        "monthly_usage": [{"month": m.month, "requests": m.requests} for m in monthly],
        "ai_usage_events": [
            {
                "provider": e.provider,
                "model": e.model,
                "tokens": e.tokens,
                "cached": e.cached,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "payment_events": [
            {
                "provider": p.provider,
                "event_type": p.event_type,
                "outcome": p.outcome,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
    }


@router.delete("/account")
def delete_my_account(
    body: DeleteAccountRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 1. The user must type the password again.
    is_valid, _ = verify_and_rehash(body.password, current_user.password_hash)
    if not is_valid:
        raise HTTPException(status_code=403, detail="Wrong password")

    uid = current_user.id
    token = credentials.credentials
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    payload = decode_access_token(token)
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

    try:
        # 2. Delete usage rows (the database blocks deleting the user while these exist).
        db.execute(delete(MonthlyUsage).where(MonthlyUsage.user_id == uid))
        db.execute(delete(AIUsageEvent).where(AIUsageEvent.user_id == uid))
        # 3. Keep payment records, but remove the link to the person.
        db.execute(update(PaymentEvent).where(PaymentEvent.user_id == uid).values(user_id=None))
        # 4. Stop the current token from working.
        db.add(RevokedToken(token_hash=token_hash, expires_at=expires_at))
        # 5. Delete the user.
        db.delete(db.get(User, uid))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not delete the account")

    return {"detail": "Account deleted"}
