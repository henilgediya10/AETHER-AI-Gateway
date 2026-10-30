# Iteration 4 — verified changes and limitations

Based on the latest Iteration 3 package and the supplied Iteration 2 audit.

- Fixed token display when provider usage is a dictionary.
- Connected the existing frontend chat history to the backend `history` field (up to 99 preceding messages).
- Preserved backend automatic provider routing (`provider: auto`); no API keys or model IDs changed.
- Corrected context trimming so the context does not start with an orphan assistant response.
- Strengthened database readiness checks to require the authentication and usage tables.
- Copied Alembic configuration and migrations into the Docker image.
- Updated the auth-route test to inspect OpenAPI paths, not FastAPI internal route representation.
- Changed the secret-packaging test so local `.env` files are allowed.

## Still pending

The 35-task roadmap is NOT complete. Production payment processing, distributed rate limits, real streaming, multi-agent orchestration, full provider testing, backups, and deployment are not delivered in this iteration.

## Important

Do not apply migrations to an existing production database without a verified backup. The original `backend/.env` is excluded from the archive.
