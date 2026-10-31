from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.user import User
from app.models.usage import MonthlyUsage
from app.services.usage import reserve_request


def test_free_plan_monthly_limit():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    try:
        with Session(engine) as db:
            user = User(
                email="quota-test@example.com",
                password_hash="test-only",
                plan="free",
                is_active=True,
            )
            db.add(user)
            db.flush()

            user_id = user.id
            month = datetime.now(timezone.utc).strftime("%Y-%m")

            db.add(
                MonthlyUsage(
                    user_id=user_id,
                    month=month,
                    requests=49,
                )
            )
            db.commit()

            used, limit = reserve_request(db, user_id)
            assert (used, limit) == (50, 50)

            with pytest.raises(HTTPException) as error:
                reserve_request(db, user_id)

            assert error.value.status_code == 429

            record = db.scalar(
                select(MonthlyUsage).where(
                    MonthlyUsage.user_id == user_id,
                    MonthlyUsage.month == month,
                )
            )
            assert record.requests == 50
    finally:
        engine.dispose()
