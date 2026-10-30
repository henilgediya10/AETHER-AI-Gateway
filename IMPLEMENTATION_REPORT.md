# AETHER implementation report — iteration 2

## Newly implemented
- Versioned Alembic baseline migration for existing `users` and `revoked_tokens` models.
- Request correlation (`X-Request-ID`) and defensive response headers.
- Production CORS allowlist from `CORS_ORIGINS` (no implicit localhost origin in production).
- CI workflow with PostgreSQL service and backend test execution.
- Regression tests for security headers and unauthorized chat.

## Important existing-database procedure
Your current PostgreSQL database already has `users` and `revoked_tokens` tables. **Do not run `alembic upgrade head` immediately on it**: the baseline migration creates those tables and would fail. First back up the database, verify the existing schema matches the baseline, then run `alembic stamp 0001_auth_baseline` ONCE to mark the existing schema as migrated. On a fresh empty database run `alembic upgrade head` instead. Stamping does not modify table structure. Test on a staging copy before production.

## Environment configuration
- `APP_ENV=production` disables automatic table creation.
- `CORS_ORIGINS=https://your-real-domain.example` sets allowed browser origin(s) in production; comma-separated for multiple origins.
- Existing API keys, provider model values and routing logic are unchanged.

## Remaining work (not claimed complete)
Persistent metering, subscription and payment integrations, distributed rate limits, multi-tenant security review, circuit breakers, tracing/metrics, production infrastructure, secrets management, end-to-end provider tests and deployment verification. All 35 tasks are **not** complete.


## Iteration 3 — customer usage and plan limits
- Added Free/Pro/Enterprise plan fields and monthly request reservations, backed by PostgreSQL.
- Added per-request provider/model/token usage events and `GET /account/usage` (JWT required).
- Added database migration `0002_usage_plans` and usage-policy tests.
- Default plan is Free (50 requests/month); Pro 2,000; Enterprise 20,000. These are configurable in code, not paid subscription entitlements. No self-service plan upgrades or payments yet.
- Failed provider calls still count against request quotas; no automatic refunds. Token counts reflect provider-reported values when available, not a billing-grade ledger.
- Existing database: **back up first**, verify/stamp 0001 only if it matches, then run `alembic upgrade head` to apply 0002. Never stamp 0002 before applying it.
- This is NOT a production certification: live PostgreSQL, payment, distributed rate limits, and provider end-to-end testing remain.
