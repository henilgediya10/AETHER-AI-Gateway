import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException

RATE_LIMITS = {
    "free": 10,
    "pro": 60,
    "enterprise": 300,
}

WINDOW_SECONDS = 60

_requests = defaultdict(deque)
_lock = Lock()


def check_rate_limit(user_id: int, plan: str):
    limit = RATE_LIMITS.get(plan, RATE_LIMITS["free"])
    now = time.monotonic()

    with _lock:
        timestamps = _requests[user_id]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= limit:
            retry_after = max(
                1,
                int(WINDOW_SECONDS - (now - timestamps[0])) + 1,
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)