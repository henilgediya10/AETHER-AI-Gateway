import os
import time
import asyncio
import random
import logging
import re
import hashlib
import json
import contextvars
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from threading import Lock
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from uuid import uuid4
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import text as sql_text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator


# ==========================================
# 1. LOAD ENVIRONMENT VARIABLES
# ==========================================

load_dotenv(
    Path(__file__).resolve().parents[1] / ".env",
    override=False
)

# Imported AFTER load_dotenv on purpose: these modules read DATABASE_URL and
# JWT_SECRET_KEY when they are imported, so .env must already be loaded.
from .router import classify, select_provider  # noqa: E402
from .database import init_db, engine, get_db  # noqa: E402
from .services.usage import reserve_request, record_success, current_usage  # noqa: E402
from .routes.auth import router as auth_router, get_current_user  # noqa: E402
from .models.user import User  # noqa: E402
from .services.monitoring_alerts import evaluate_alerts  # noqa: E402

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PROD = APP_ENV == "production"


# ==========================================
# TASK 28: STRUCTURED LOGGING
# ==========================================

# Every log line carries the id of the HTTP request that produced it.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# Secrets must never reach the logs or the client, whatever the provider echoes back.
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),            # Google
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),             # OpenAI / Anthropic
    re.compile(r"gsk_[A-Za-z0-9]{12,}"),              # Groq
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{10,}", re.I),
]


def redact(value) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# Attributes every LogRecord has; anything else came from `extra=` and is logged.
_STANDARD_LOG_FIELDS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JSONFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": request_id_var.get(),
        }

        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and not key.startswith("_"):
                data[key] = value

        if record.exc_info:
            data["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(data, default=str, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())

# Provider metrics are implemented here, without importing app.services.metrics.
# This avoids the broken import in the existing services/metrics.py.
import math


class ProviderMetrics(logging.Handler):
    """In-process provider statistics from existing structured log events."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self._data_lock = Lock()
        self._data = {}
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._started = time.monotonic()

    def emit(self, record):
        event = getattr(record, "event", None)
        provider = getattr(record, "provider", None)
        if event not in {"provider_call_success", "provider_call_failure", "provider_cache_hit"}:
            return
        if not isinstance(provider, str) or not provider or len(provider) > 50:
            return
        try:
            with self._data_lock:
                if provider not in self._data:
                    if len(self._data) >= 50:
                        return
                    self._data[provider] = {
                        "success": 0, "failure": 0, "cache_hits": 0,
                        "tokens_total": 0, "latency_sum": 0.0,
                        "latency_count": 0, "max_latency": 0.0,
                        "success_latencies": deque(maxlen=1000),
                        "failures_by_status": defaultdict(int),
                    }
                data = self._data[provider]
                if event == "provider_cache_hit":
                    data["cache_hits"] += 1
                    return
                if event == "provider_call_success":
                    data["success"] += 1
                    tokens = getattr(record, "tokens_total", 0)
                    if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and math.isfinite(tokens):
                        data["tokens_total"] += max(0, int(tokens))
                else:
                    data["failure"] += 1
                    status = getattr(record, "status", None)
                    status_key = str(status) if isinstance(status, int) and not isinstance(status, bool) else "unknown"
                    if status_key not in data["failures_by_status"] and len(data["failures_by_status"]) >= 20:
                        status_key = "other"
                    data["failures_by_status"][status_key] += 1
                latency = getattr(record, "latency_ms", None)
                if isinstance(latency, (int, float)) and not isinstance(latency, bool) and math.isfinite(latency) and latency >= 0:
                    data["latency_sum"] += latency
                    data["latency_count"] += 1
                    data["max_latency"] = max(data["max_latency"], latency)
                    if event == "provider_call_success":
                        data["success_latencies"].append(latency)
        except Exception:
            self.handleError(record)

    def snapshot(self):
        with self._data_lock:
            providers = {}
            for name, data in self._data.items():
                total = data["success"] + data["failure"]
                latencies = sorted(data["success_latencies"])

                def percentile(q):
                    if not latencies:
                        return None
                    return round(latencies[max(0, math.ceil(q / 100 * len(latencies)) - 1)], 2)

                providers[name] = {
                    "total_attempts": total,
                    "successful_attempts": data["success"],
                    "failed_attempts": data["failure"],
                    "success_rate_percent": round(data["success"] / total * 100, 2) if total else None,
                    "average_latency_ms": round(data["latency_sum"] / data["latency_count"], 2) if data["latency_count"] else None,
                    "max_latency_ms": round(data["max_latency"], 2),
                    "success_latency_p50_ms": percentile(50),
                    "success_latency_p95_ms": percentile(95),
                    "tokens_total": data["tokens_total"],
                    "cache_hits": data["cache_hits"],
                    "failures_by_status": dict(data["failures_by_status"]),
                }
            return {
                "scope": "current_server_process",
                "since": self._started_at,
                "uptime_seconds": round(time.monotonic() - self._started),
                "providers": providers,
            }


metrics = ProviderMetrics()


def install_metrics(logger):
    if not any(isinstance(h, ProviderMetrics) for h in logger.handlers):
        logger.addHandler(metrics)


log = logging.getLogger("aether")
log.handlers.clear()
log.addHandler(handler)
_requested_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO", logging.INFO)
LOG_LEVEL = _requested_level if isinstance(_requested_level, int) else logging.INFO
handler.setLevel(LOG_LEVEL)
log.setLevel(min(LOG_LEVEL, logging.INFO))
log.propagate = False
install_metrics(log)


# ==========================================
# 2. CONFIGURATION CHECKS (FAIL FAST)
# ==========================================

KEYS = {
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY"
}

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]


def validate_config():
    """Refuse to start a production server that is missing critical settings."""
    if IS_PROD:
        missing = [
            name for name in ("DATABASE_URL", "JWT_SECRET_KEY")
            if not os.getenv(name, "").strip()
        ]
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        if not CORS_ORIGINS:
            log.warning("CORS_ORIGINS is empty: browsers will be blocked from the API")

    if not any(os.getenv(name, "").strip() for name in KEYS.values()):
        log.warning("No AI provider API keys are configured")


validate_config()


# ==========================================
# 3. CREATE FASTAPI APPLICATION
# ==========================================

_http_client = None


def get_http_client() -> httpx.AsyncClient:
    """One shared client: connection pooling and no TLS handshake per request."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20
            ),
        )
    return _http_client


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()


# Interactive docs are useful in development; hide them in production
# unless ENABLE_DOCS=true is set on purpose.
_docs_enabled = (not IS_PROD) or os.getenv("ENABLE_DOCS", "").lower() == "true"

app = FastAPI(
    title="AETHER AI Gateway",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Development bootstrap only. Production schema changes require Alembic migrations.
if not IS_PROD:
    init_db()

# User registration / login API
from .routes.billing import router as billing_router  # noqa: E402
app.include_router(auth_router)
app.include_router(billing_router)


@app.get("/admin/metrics", tags=["Operations"])
def admin_metrics(request: Request):
    import secrets

    expected = os.getenv("AETHER_METRICS_TOKEN", "")
    supplied = request.headers.get("X-Metrics-Token", "")

    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Metrics access denied")

    snapshot = metrics.snapshot()
    return {**snapshot, "alerts": evaluate_alerts(snapshot)}


@app.get("/health/live", tags=["Operations"])
def liveness():
    """Liveness is independent of the database and external AI providers."""
    return {"status": "alive"}


@app.get("/health/ready", tags=["Operations"])
def readiness():
    """Readiness requires a working database; no secrets are exposed."""
    try:
        with engine.connect() as connection:
            connection.execute(sql_text("SELECT 1"))
            required = {"users", "revoked_tokens", "monthly_usage", "ai_usage_events"}
            existing = set(inspect(connection).get_table_names())
            if not required.issubset(existing):
                raise HTTPException(status_code=503, detail="Database schema not ready")
    except SQLAlchemyError:
        log.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ready"}


# Request tracing and baseline browser hardening. Do not log JWTs, API keys
# or prompt text.
@app.middleware("http")
async def security_and_request_id(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    started = time.perf_counter()

    try:
        try:
            response = await call_next(request)
        except Exception:
            # Unhandled error: log the traceback once, return a safe 500 that
            # still carries the request id so support can find the log line.
            log.exception(
                "HTTP request failed: method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                request_id,
                extra={"method": request.method, "path": request.url.path},
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
            )

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "HTTP request completed: method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if IS_PROD:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    finally:
        request_id_var.reset(token)


# ==========================================
# REDIS DISTRIBUTED RATE LIMITING
# ==========================================
import uuid

import redis

RATE_LIMITS = {"free": 10, "pro": 60, "enterprise": 300}
WINDOW_SECONDS = 60

redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
    socket_connect_timeout=2,
    socket_timeout=2,
    decode_responses=True,
)

# Atomic rolling window on Redis server time (microseconds), so every
# worker/instance shares one clock and one counter per user.
RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local member = ARGV[3]

local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000000 + tonumber(t[2])
local cutoff = now - window * 1000000

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry = math.max(1, math.ceil(
        (tonumber(oldest[2]) + window * 1000000 - now) / 1000000
    ))
    return {0, retry}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 1)
return {1, 0}
"""


def check_rate_limit(user_id: int, plan: str) -> None:
    """Limit requests per user in a rolling 60-second window (shared via Redis)."""
    limit = RATE_LIMITS.get(plan, RATE_LIMITS["free"])
    key = f"aether:rate:{user_id}"

    try:
        allowed, retry_after = redis_client.eval(
            RATE_LIMIT_SCRIPT,
            1,
            key,
            limit,
            WINDOW_SECONDS,
            uuid.uuid4().hex,
        )
    except redis.RedisError:
        # Fail closed: never let a Redis outage disable rate limiting.
        log.error("Rate limiter unavailable: Redis error", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Rate limiting service unavailable. Please try again shortly.",
            headers={"Retry-After": "5"},
        )

    if not int(allowed):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again shortly.",
            headers={"Retry-After": str(int(retry_after))},
        )


# ==========================================
# 4. FRONTEND CORS CONFIGURATION
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        CORS_ORIGINS
        if IS_PROD
        else [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
        ]
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ==========================================
# ADMIN DASHBOARD: authenticated database analytics
# ==========================================

from sqlalchemy import select as admin_select, func as admin_func
from .models.usage import MonthlyUsage, AIUsageEvent

def require_admin(user: User = Depends(get_current_user)):
    # Explicit allowlist; never trust a user-supplied plan or role.
    emails = {e.strip().lower() for e in os.getenv("AETHER_ADMIN_EMAILS", "").split(",") if e.strip()}
    if not emails or user.email.lower() not in emails:
        raise HTTPException(status_code=403, detail="Admin access denied")
    return user


@app.get("/admin/analytics/daily", tags=["Admin"])
def admin_daily_analytics(
    days: int = Query(30, ge=1, le=365),
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Successful AI requests and tokens per UTC day for the last `days` days."""
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days - 1)
    start_time = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)

    events = db.execute(
        admin_select(AIUsageEvent.created_at, AIUsageEvent.tokens)
        .where(AIUsageEvent.created_at >= start_time)
    ).all()

    daily = defaultdict(lambda: {"requests": 0, "tokens": 0})
    for created_at, tokens in events:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        day = created_at.astimezone(timezone.utc).date().isoformat()
        daily[day]["requests"] += 1
        daily[day]["tokens"] += tokens or 0

    results = []
    for i in range(days):
        date = (start_date + timedelta(days=i)).isoformat()
        results.append({
            "date": date,
            "requests": daily[date]["requests"],
            "tokens": daily[date]["tokens"],
        })

    return {"period_days": days, "timezone": "UTC", "daily": results}


@app.get("/admin/overview", tags=["Admin"])
def admin_overview(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    users = db.scalar(admin_select(admin_func.count(User.id))) or 0
    active = db.scalar(admin_select(admin_func.count(User.id)).where(User.is_active.is_(True))) or 0
    plans = db.execute(admin_select(User.plan, admin_func.count(User.id)).group_by(User.plan)).all()
    requests_month = db.scalar(admin_select(admin_func.coalesce(admin_func.sum(MonthlyUsage.requests), 0)).where(MonthlyUsage.month == month)) or 0
    successes = db.scalar(admin_select(admin_func.count(AIUsageEvent.id))) or 0
    tokens = db.scalar(admin_select(admin_func.coalesce(admin_func.sum(AIUsageEvent.tokens), 0))) or 0
    providers = db.execute(admin_select(AIUsageEvent.provider, admin_func.count(AIUsageEvent.id), admin_func.coalesce(admin_func.sum(AIUsageEvent.tokens), 0)).group_by(AIUsageEvent.provider)).all()
    return {"registered_users": users, "enabled_accounts": active, "plans": {p: n for p,n in plans}, "month": month, "requests_this_month": requests_month, "successful_ai_events_all_time": successes, "tokens_all_time": tokens, "providers": [{"provider": p, "successful_calls": n, "tokens": t} for p,n,t in providers], "provider_metrics_current_process": metrics.snapshot()}


@app.get("/admin/users", tags=["Admin"])
def admin_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    # Aggregate separately to avoid multiplying rows across event and monthly tables.
    monthly = dict(db.execute(admin_select(MonthlyUsage.user_id, MonthlyUsage.requests).where(MonthlyUsage.month == month)).all())
    token_rows = db.execute(admin_select(AIUsageEvent.user_id, admin_func.count(AIUsageEvent.id), admin_func.coalesce(admin_func.sum(AIUsageEvent.tokens), 0)).group_by(AIUsageEvent.user_id)).all()
    totals = {uid: (count, tokens) for uid,count,tokens in token_rows}
    users = db.scalars(admin_select(User).order_by(User.id).limit(500)).all()
    return {"month": month, "limit": 500, "users": [{"id": u.id, "email": u.email, "plan": u.plan, "enabled": u.is_active, "registered_at": u.created_at.isoformat() if u.created_at else None, "requests_this_month": monthly.get(u.id, 0), "successful_ai_calls_all_time": totals.get(u.id, (0,0))[0], "tokens_all_time": totals.get(u.id, (0,0))[1]} for u in users]}

# ==========================================
# 5. AI PROVIDER CONFIGURATION
# ==========================================

MODELS = {
    # "gemini-flash-latest" is Google's documented alias. Once you have checked
    # the exact model id in Google AI Studio, pin it with GEMINI_MODEL.
    "gemini": os.getenv(
        "GEMINI_MODEL", "gemini-flash-latest"
    ),
    "openai": os.getenv(
        "OPENAI_MODEL", "gpt-4o-mini"
    ),
    "anthropic": os.getenv(
        "ANTHROPIC_MODEL",
        "claude-haiku-5-5"
    ),
    "groq": os.getenv(
        "GROQ_MODEL",
        "llama-3.3-70b-versatile"
    )
}

COOLDOWN = {}
CACHE = {}

CACHE_TTL = int(
    os.getenv("CACHE_TTL_SECONDS", "300")
)

CACHE_MAX_ENTRIES = int(
    os.getenv("CACHE_MAX_ENTRIES", "256")
)

MAX_CONTEXT_TOKENS = int(
    os.getenv("MAX_CONTEXT_TOKENS", "6000")
)

# Cap the answer length for every provider (cost and latency control).
MAX_OUTPUT_TOKENS = int(
    os.getenv("MAX_OUTPUT_TOKENS", "1024")
)

# Retry policy: at most 3 tries per provider, never sleep longer than this
# inside a user request; a longer Retry-After means "fail over now".
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
MAX_RETRY_SLEEP = float(os.getenv("MAX_RETRY_SLEEP_SECONDS", "8"))

# Providers tried after the selected one. Default keeps the old behaviour (Gemini only).
FALLBACK_PROVIDERS = [
    p.strip()
    for p in os.getenv("FALLBACK_PROVIDERS", "gemini").split(",")
    if p.strip() in KEYS
]


# ==========================================
# 6. TOKEN ESTIMATION
# ==========================================

def estimate_tokens(text):
    # Approximation; provider usage is authoritative.
    return max(1, (len(text) + 3) // 4)


def total_tokens(tokens) -> int:
    """Single integer for usage accounting, whatever the provider reported."""
    if not isinstance(tokens, dict):
        return 0
    total = tokens.get("total")
    if isinstance(total, int):
        return total
    return sum(
        value for value in (tokens.get("input"), tokens.get("output"))
        if isinstance(value, int)
    )


def cache_key(provider, model, messages):
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "messages": messages
        },
        sort_keys=True,
        ensure_ascii=False
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


# ==========================================
# 7. REQUEST SCHEMAS
# ==========================================

MAX_HISTORY_CHARS = int(os.getenv("MAX_HISTORY_CHARS", "200000"))


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(
        min_length=1,
        max_length=30000
    )


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=30000
    )

    provider: Literal[
        "auto",
        "gemini",
        "openai",
        "anthropic",
        "groq"
    ] = "auto"

    history: list[Message] = Field(
        default_factory=list,
        max_length=100
    )

    @field_validator("history")
    @classmethod
    def history_not_too_large(cls, value):
        # 100 x 30,000 chars would be ~3 MB per request; cap the total.
        if sum(len(m.content) for m in value) > MAX_HISTORY_CHARS:
            raise ValueError("History is too large")
        return value


# ==========================================
# 8. PROVIDER ERROR HANDLING
# ==========================================

class ProviderError(Exception):

    def __init__(
        self,
        provider,
        status,
        message,
        retry_after=0
    ):
        self.provider = provider
        self.status = status
        self.message = message
        self.retry_after = retry_after

        super().__init__(message)


def key_for(provider):
    return os.getenv(
        KEYS[provider], ""
    ).strip()


def retry_seconds(value):

    if not value:
        return 0

    try:
        return max(
            0,
            min(120, float(value))
        )

    except ValueError:
        try:
            return max(
                0,
                min(
                    120,
                    (
                        parsedate_to_datetime(value)
                        - datetime.now(timezone.utc)
                    ).total_seconds()
                )
            )

        except Exception:
            return 0


# ==========================================
# 9. CONTEXT OPTIMIZATION
# ==========================================

def _merge_same_role(messages):
    """Anthropic and Gemini reject two consecutive messages from the same role."""
    merged = []
    for m in messages:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    return merged


def compact(history, message):

    result = [
        {
            "role": m.role,
            "content": m.content
        }
        for m in history
    ]

    result.append({
        "role": "user",
        "content": message
    })

    result = result[-24:]
    # A trimmed conversation must never start with an orphan assistant reply.
    while len(result) > 1 and result[0]["role"] == "assistant":
        result.pop(0)

    while (
        len(result) > 1
        and sum(
            estimate_tokens(m["content"]) + 4
            for m in result
        ) > MAX_CONTEXT_TOKENS
    ):
        result.pop(0)
        while len(result) > 1 and result[0]["role"] == "assistant":
            result.pop(0)

    return result[-24:]


# ==========================================
# 10. CALL AI PROVIDERS
# ==========================================

async def call_provider(
    provider,
    messages,
    client
):

    key = key_for(provider)

    if not key:
        raise ProviderError(
            provider,
            0,
            "Provider API key is missing"
        )

    model = MODELS[provider]

    # Providers reject two consecutive messages with the same role, so merge
    # them here (compact() keeps its own contract and is left untouched).
    messages = _merge_same_role(messages)

    headers = {
        "Content-Type": "application/json"
    }

    if provider == "gemini":

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{model}:generateContent"
        )

        headers["x-goog-api-key"] = key

        body = {
            "contents": [
                {
                    "role": (
                        "model"
                        if m["role"] == "assistant"
                        else "user"
                    ),
                    "parts": [
                        {
                            "text": m["content"]
                        }
                    ]
                }
                for m in messages
            ],
            "generationConfig": {
                "maxOutputTokens": MAX_OUTPUT_TOKENS
            }
        }

    elif provider in ("openai", "groq"):

        if provider == "openai":
            url = (
                "https://api.openai.com/"
                "v1/chat/completions"
            )
            limit_field = "max_completion_tokens"
        else:
            url = (
                "https://api.groq.com/openai/"
                "v1/chat/completions"
            )
            limit_field = "max_tokens"

        headers["Authorization"] = (
            "Bearer " + key
        )

        body = {
            "model": model,
            "messages": messages,
            limit_field: MAX_OUTPUT_TOKENS
        }

    else:

        url = (
            "https://api.anthropic.com/"
            "v1/messages"
        )

        headers.update({
            "x-api-key": key,
            "anthropic-version": "2023-06-01"
        })

        body = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": messages
        }

    try:
        response = await client.post(
            url,
            headers=headers,
            json=body
        )

    except (
        httpx.TimeoutException,
        httpx.TransportError
    ) as error:

        raise ProviderError(
            provider,
            503,
            type(error).__name__
        ) from None

    if response.status_code >= 400:

        try:
            obj = response.json()
            err = obj.get("error", obj)

            detail = (
                err.get(
                    "message",
                    "Provider request failed"
                )
                if isinstance(err, dict)
                else str(err)
            )

        except Exception:
            detail = (
                "Provider returned an invalid "
                "error response"
            )

        detail = redact(detail)[:400]

        raise ProviderError(
            provider,
            response.status_code,
            detail,
            retry_seconds(
                response.headers.get("Retry-After")
            )
        )

    try:
        data = response.json()

        if provider == "gemini":

            answer = "".join(
                part.get("text", "")
                for candidate in data.get(
                    "candidates", []
                )
                for part in candidate.get(
                    "content", {}
                ).get("parts", [])
            )

            usage = data.get(
                "usageMetadata", {}
            )

            tokens = {
                "input": usage.get(
                    "promptTokenCount"
                ),
                "output": usage.get(
                    "candidatesTokenCount"
                ),
                "total": usage.get(
                    "totalTokenCount"
                )
            }

            model = (
                data.get("modelVersion")
                or model
            )

        elif provider in (
            "openai", "groq"
        ):

            answer = (
                data["choices"][0]["message"]
                .get("content") or ""
            )

            usage = data.get("usage", {})

            tokens = {
                "input": usage.get(
                    "prompt_tokens"
                ),
                "output": usage.get(
                    "completion_tokens"
                ),
                "total": usage.get(
                    "total_tokens"
                )
            }

            model = (
                data.get("model") or model
            )

        else:

            answer = "".join(
                item.get("text", "")
                for item in data.get(
                    "content", []
                )
                if item.get("type") == "text"
            )

            usage = data.get("usage", {})

            tokens = {
                "input": usage.get(
                    "input_tokens"
                ),
                "output": usage.get(
                    "output_tokens"
                ),
                "total": None
            }

            model = (
                data.get("model") or model
            )

    except (
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError
    ) as error:

        raise ProviderError(
            provider,
            502,
            "Invalid provider response"
        ) from error

    if not answer:
        # Typical cause: the provider's safety filter blocked THIS prompt.
        # Status 422 is not retried and does not start a provider-wide cooldown,
        # so one blocked prompt cannot take the provider down for every user.
        raise ProviderError(
            provider,
            422,
            "Empty model output"
        )

    return {
        "answer": answer,
        "model": model,
        "tokens": tokens
    }


# ==========================================
# 11. RETRIES, COOLDOWN AND CACHE
# ==========================================

def _cache_get(ck):
    cached = CACHE.get(ck)
    if cached and time.monotonic() - cached["at"] < CACHE_TTL:
        return cached["result"]
    CACHE.pop(ck, None)
    return None


def _cache_set(ck, result):
    CACHE[ck] = {
        "at": time.monotonic(),
        "result": result
    }

    if len(CACHE) > CACHE_MAX_ENTRIES:
        oldest = min(CACHE, key=lambda k: CACHE[k]["at"])
        CACHE.pop(oldest, None)


async def attempt(
    provider,
    messages,
    client
):

    remaining = COOLDOWN.get(provider, 0) - time.monotonic()

    if remaining > 0:
        raise ProviderError(
            provider,
            429,
            "Provider cooling down",
            remaining
        )

    ck = cache_key(
        provider,
        MODELS[provider],
        messages
    )

    cached = _cache_get(ck)

    if cached is not None:
        log.info(
            "provider_cache_hit",
            extra={
                "event": "provider_cache_hit",
                "provider": provider,
                "model": MODELS[provider],
                "cached": True,
            },
        )
        return {
            **cached,
            "cached": True
        }

    for n in range(MAX_ATTEMPTS):

        provider_started = time.perf_counter()

        try:

            result = await call_provider(
                provider,
                messages,
                client
            )

        except ProviderError as error:

            level = (
                logging.ERROR
                if error.status in (401, 403)  # bad key / no access: needs a human
                else logging.WARNING
            )
            log.log(
                level,
                "provider_call_failure",
                extra={
                    "event": "provider_call_failure",
                    "provider": provider,
                    "model": MODELS[provider],
                    "status": error.status,
                    "error": redact(error.message)[:200],
                    "latency_ms": round(
                        (time.perf_counter() - provider_started) * 1000, 2
                    ),
                    "cached": False,
                    "attempt_number": n + 1,
                },
            )

            if error.status not in RETRYABLE_STATUSES:
                raise

            delay = max(
                error.retry_after,
                min(
                    MAX_RETRY_SLEEP,
                    2 ** n + random.random()
                )
            )

            # Last try, or the provider wants us to wait longer than a user
            # request should: stop, start the cooldown and let the caller fail over.
            if n == MAX_ATTEMPTS - 1 or delay > MAX_RETRY_SLEEP:

                COOLDOWN[provider] = (
                    time.monotonic()
                    + max(15, delay)
                )

                raise

            await asyncio.sleep(delay)
            continue

        COOLDOWN.pop(
            provider, None
        )

        log.info(
            "provider_call_success",
            extra={
                "event": "provider_call_success",
                "provider": provider,
                "model": result.get("model", MODELS[provider]),
                "status": "success",
                "latency_ms": round(
                    (time.perf_counter() - provider_started) * 1000, 2
                ),
                "cached": False,
                "tokens_total": total_tokens(result.get("tokens")),
                "attempt_number": n + 1,
            },
        )

        _cache_set(ck, result)

        return {
            **result,
            "cached": False
        }


# ==========================================
# 12. HEALTH CHECK
# ==========================================

@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# ==========================================
# 13. PROVIDER STATUS
# ==========================================

@app.get("/providers/status")
def status():

    return {
        provider: {
            "configured": bool(
                key_for(provider)
            ),
            "cooldown_seconds": round(
                max(
                    0,
                    COOLDOWN.get(
                        provider, 0
                    ) - time.monotonic()
                )
            )
        }
        for provider in KEYS
    }


# ==========================================
# 14. AVAILABLE MODELS
# ==========================================

@app.get("/models")
def models():

    return MODELS


# ==========================================
# 15. ACCOUNT USAGE
# ==========================================

@app.get("/account/usage", tags=["Account"])
def account_usage(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return current_usage(db, current_user)


# ==========================================
# 16. MAIN AI CHAT ENDPOINT
# ==========================================

def _public_failure_reason(status: int) -> str:
    """Client-safe reason. Raw provider messages stay in the server logs."""
    if status == 0:
        return "not configured"
    if status == 429:
        return "rate limited"
    if status >= 500:
        return "provider unavailable"
    return "request rejected"


@app.post("/chat")
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    # Redis call is blocking: keep it off the event loop.
    await run_in_threadpool(
        check_rate_limit, current_user.id, current_user.plan
    )

    # reserve_request / record_success are blocking database calls. Run them in
    # the thread pool so one slow query cannot freeze every other request.
    used, request_limit = await run_in_threadpool(
        reserve_request, db, current_user.id
    )
    started = time.perf_counter()

    # classify() may load/run an embedding model (USE_QWEN=true): keep it off the event loop.
    category = await run_in_threadpool(classify, req.message)

    selected = select_provider(
        category,
        req.provider,
        [p for p in KEYS if key_for(p)]
    )

    sequence = [selected] + [
        p for p in FALLBACK_PROVIDERS if p != selected
    ]

    messages = compact(
        req.history,
        req.message
    )

    failures = []

    client = get_http_client()

    for provider in sequence:

        if not key_for(provider):

            failures.append({
                "provider": provider,
                "status": 0,
                "reason": _public_failure_reason(0)
            })

            continue

        try:

            result = await attempt(
                provider,
                messages,
                client
            )

        except ProviderError as error:

            failures.append({
                "provider": provider,
                "status": error.status,
                "reason": _public_failure_reason(error.status)
            })

            continue

        cached = result.get("cached", False)

        # The model already answered. A usage-accounting failure must not
        # throw that answer away, but it must be visible in the logs.
        try:
            await run_in_threadpool(
                record_success,
                db,
                current_user.id,
                provider,
                result["model"],
                total_tokens(result.get("tokens")),
                cached,
            )
        except Exception:
            log.exception(
                "Failed to record usage",
                extra={"user_id": current_user.id, "provider": provider},
            )

        latency_ms = round(
            (time.perf_counter() - started) * 1000
        )

        log.info(
            "chat completed",
            extra={
                "event": "chat_completed",
                "user_id": current_user.id,
                "provider": provider,
                "model": result["model"],
                "category": category,
                "fallback": provider != selected,
                "cached": cached,
                "latency_ms": latency_ms,
            },
        )

        return {
            "monthly_requests_used": used,
            "monthly_requests_limit": request_limit,
            "reply": result["answer"],
            "provider": provider,
            "model": result["model"],
            "category": category,
            "selected_provider": selected,
            "fallback": provider != selected,
            "tokens": result["tokens"],
            "cached": cached,
            "estimated_context_tokens": sum(
                estimate_tokens(
                    m["content"]
                ) + 4
                for m in messages
            ),
            "latency_ms": latency_ms,
            "notice": (
                f"Generated by {provider.title()}"
                if provider != "gemini"
                else "Generated by Google Gemini"
            )
        }

    log.error(
        "chat failed: all providers failed",
        extra={"user_id": current_user.id, "failures": failures},
    )

    raise HTTPException(
        status_code=503,
        detail={
            "message": (
                "No configured provider could "
                "complete this request"
            ),
            "failures": failures
        }
    )
from .routes.account import router as account_router  # noqa: E402
app.include_router(account_router)
