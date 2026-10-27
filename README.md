# AETHER AI Gateway

FastAPI backend and single-file HTML/CSS/JS frontend. Runs on Windows 11 / Python 3.12.

## Start (PowerShell)

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
uvicorn app.main:app --reload --port 8000
```

Enter your own existing Google API key into `backend/.env` (never share or commit it). In a **second** PowerShell terminal:

```powershell
cd frontend
py -3.12 -m http.server 5173
```

Open http://localhost:5173 .

## Optional Qwen classification

In the backend environment run `pip install -r requirements-qwen.txt`, set `USE_QWEN=true` in `backend/.env`, then restart the backend. The first run downloads model weights; CPU inference can be slow. Without it, classification uses fast heuristics. No local generative model is required.

## Tests

```powershell
cd backend
pip install pytest
pytest -q
```

No real provider calls are made by the included tests. The application does not auto-retry pending requests indefinitely. Cooldown is process-local; restarting clears it. Browser history is stored in localStorage and can be lost when clearing browser data. API usage is reported only when the provider returns it. No fictional quota or price estimates are displayed. Context trimming is conservative, not exact tokenizer budgeting. A production deployment needs authentication, persistent sessions, Redis-backed cooldown, encryption, and provider-specific model configuration.

## Verification results (2026-10-27)

23 automated tests passed using mocked providers; Python compilation and JavaScript syntax passed. Live Gemini requests, real Qwen inference, Windows installation, and real-browser end-to-end interactions were NOT TESTED. These require the user environment and explicit permission for live Gemini calls. The application is a starter and is not production validated.

## Verification and limits (2026-10-27)

Run `cd backend && python -m pytest -q` for offline regression tests. The suite includes 100 context-history sizes, mocked provider request/response formats, classification smoke tests, and retry cases. **Offline tests do not prove response quality, cost savings, live Gemini access, real Qwen CPU accuracy, or Windows browser compatibility.** Those require separate live benchmarks on your own machine.

The history compaction uses a conservative **character budget**, not exact model token counting. Actual input/output token counts are reported only if returned by a provider. No demonstrated percentage token savings or answer-quality improvement should be inferred from the passing tests.
