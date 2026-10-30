# AETHER AI Gateway — Auth-integrated build

This build preserves the existing provider router, Gemini configuration, fallback and telemetry code. The browser now supports account registration, login, protected chat and server-side JWT logout.

## Files changed

- `backend/app/main.py`: removed duplicate early authentication imports; existing JWT-protected `/chat` retained.
- `backend/app/services/security.py`: adds a random `jti` per login, so two sessions cannot accidentally share an identical JWT. The JWT signing key and algorithm are unchanged.
- `backend/requirements.txt`: adds the missing PostgreSQL, SQLAlchemy, PyJWT, Argon2 password-hashing and email-validation dependencies.
- `frontend/index.html`: adds registration/login/logout panel, attaches `Authorization: Bearer <JWT>` to `/chat`, and handles expired/revoked sessions. JWT stays in memory and is never written to localStorage. Existing gateway design and provider request contract are retained.
- `backend/tests/test_auth_integration.py`: checks token uniqueness, protected chat and route registration. Legacy provider tests are adjusted to mock an authenticated user when exercising the provider logic.

## Secrets

Your original `backend/.env` and `.git` directory were deliberately **excluded** from the downloadable ZIP. The existing `.env` on your computer must stay in place. Do not copy example credentials over it or upload it publicly. The downloaded project cannot make live provider requests without your local configuration.

## Windows PowerShell — run

1. Extract ZIP to a **new folder** (do not overwrite your working project yet).
2. Copy your existing `backend/.env` from your original project into the new project's `backend` folder, **locally on your PC**. Do not share it.
3. Start Docker Desktop and run `docker start aether-postgres` (if your database is not already running).
4. From the new project's `backend` folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app.main:app --reload --port 8000
```

5. In another terminal, from `frontend`:

```powershell
python -m http.server 5173
```

6. Open http://localhost:5173, register a new customer, send a message, log out, and verify a fresh login is required. Open http://localhost:8000/docs for API checks.

## Important security / production limitations

- The browser's JWT is **memory-only**, so refreshing the page requires login again. This avoids persistent browser-token storage but is not equivalent to HttpOnly cookie sessions.
- Chat history is scoped by account ID in localStorage, but is **not encrypted** and is not synchronized across devices. Avoid shared computers; for real multi-tenant production, use authenticated server-side storage and retention controls.
- Older pre-login chat history keys are not migrated into customer accounts.
- PostgreSQL tables are still created with `create_all()` on application startup. Use Alembic migrations before production deployment.
- No rate limiting, CSRF protections for a future cookie-based auth system, email verification, password reset, subscription billing, usage quotas, Redis-backed provider cooldown, distributed cache or HTTPS/reverse-proxy setup is included.
- Backend CORS is configured for localhost:5173; configure explicit HTTPS origins for deployment.
- Browser relies on CDN-hosted JS/CSS libraries; pin assets, enforce CSP and review supply-chain risk before public launch.
- Real Gemini/provider calls, PostgreSQL Docker on Windows, and real-browser end-to-end interactions have **not** been run here. Test in your environment before launch.
