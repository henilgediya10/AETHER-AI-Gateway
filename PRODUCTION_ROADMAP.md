# AETHER AI Gateway — production work tracker

This is a **working upgrade**, not a claim that all 35 requested tasks are done. The exact 35-item checklist has not been supplied.

## Implemented or preserved
- Existing provider routing, fallback, classification and token telemetry: preserved without changes to model names or keys.
- Existing PostgreSQL user authentication, JWT logout revocation, and frontend authentication: preserved.
- **New:** `/health/live` liveness and `/health/ready` database readiness endpoints.
- **New:** `APP_ENV=production` disables implicit `create_all()` on application startup. Run a real schema migration before production deployment.
- **New:** non-root backend Dockerfile, `.dockerignore`, and health endpoint tests.

## Must complete before a real production launch
1. Define and implement the exact 35-task acceptance criteria.
2. Versioned Alembic migrations and rollback strategy; **production will not auto-create tables**.
3. Persistent per-user usage metering and enforced plan quotas.
4. Billing and payment provider webhook signature verification, idempotency and reconciliation.
5. API rate limits and brute-force protection with shared storage (e.g., Redis).
6. Multi-tenant isolation, authorization, and customer data deletion/export.
7. Cross-worker-safe provider circuit breakers, caching, retry budgets and quota recovery.
8. Structured logging, trace correlation, metrics, alerting and audit logs without secrets.
9. Secure browser sessions, CSRF/XSS controls, HTTPS, strict CORS and secure response headers.
10. Dependency locking, vulnerability scanning, CI, integration tests, load tests and deployment smoke tests.
11. Managed PostgreSQL, backups/restore drills, secret manager, deployment pipeline and rollback.
12. Real Gemini and other-provider end-to-end tests with user-supplied credentials in their own environment.

## Local run
Use your existing `backend/.env` locally; never commit it. In `backend/`:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --reload --port 8000
```

Frontend in another terminal from `frontend/`:

```powershell
python -m http.server 5173
```

Check `http://localhost:8000/health/live` and `http://localhost:8000/health/ready`.

**Production caveat:** `APP_ENV=production` requires tables to be created via a separately managed migration process. The Dockerfile is a starting point, not a complete production deployment.
