"""Per-user monthly quota enforcement and provider usage accounting.

Requests are reserved BEFORE provider calls, including failures, to prevent abuse.
Plan upgrades require trusted admin/payment logic and cannot be self-assigned.
"""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.user import User
from app.models.usage import MonthlyUsage, AIUsageEvent

PLAN_LIMITS = {"free": 50, "pro": 2000, "enterprise": 20000}

def limit_for(plan: str) -> int:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

def reserve_request(db: Session, user_id: int) -> tuple[int, int]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        # PostgreSQL row lock prevents simultaneous requests bypassing quotas.
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or not user.is_active:
            raise HTTPException(401, "Account unavailable")
        limit = limit_for(user.plan)
        record = db.scalar(select(MonthlyUsage).where(MonthlyUsage.user_id == user_id, MonthlyUsage.month == month))
        if record is None:
            record = MonthlyUsage(user_id=user_id, month=month, requests=0)
            db.add(record)
        if record.requests >= limit:
            raise HTTPException(429, "Monthly request limit reached. Upgrade your plan or wait for next month.")
        record.requests += 1
        used = record.requests
        db.commit()
        return used, limit
    except Exception:
        db.rollback()
        raise

def normalize_tokens(usage) -> int:
    """Accept existing provider token dictionaries without changing their response contract."""
    if isinstance(usage, dict):
        value = usage.get("total")
        if value is None:
            value = (usage.get("input") or 0) + (usage.get("output") or 0)
    else:
        value = usage
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError):
        return 0


def record_success(db: Session, user_id: int, provider: str, model: str, tokens, cached: bool) -> None:
    db.add(AIUsageEvent(user_id=user_id, provider=provider, model=model, tokens=normalize_tokens(tokens), cached=bool(cached)))
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

def current_usage(db: Session, user: User) -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    record = db.scalar(select(MonthlyUsage).where(MonthlyUsage.user_id == user.id, MonthlyUsage.month == month))
    used = record.requests if record else 0
    limit = limit_for(user.plan)
    return {"plan": user.plan, "month": month, "requests_used": used, "requests_limit": limit, "requests_remaining": max(0, limit - used)}
