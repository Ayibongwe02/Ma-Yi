# Ma-yi Sentinel

Live Command desk (React) + Stage-4 Python engine (yfinance, patterns, ML / anomaly, orders).

**One container, one port** — FastAPI serves the API and the production SPA.

## Quick start (Docker — recommended)

```bash
docker compose up --build
# open http://localhost:8000
# health: curl -f http://localhost:8000/api/health
```

Click **Scan now** to run the engine on yfinance data. Auto-scan runs hourly by default.

```bash
# optional secrets
cp backend/config.example.env .env
# edit TELEGRAM_*, OANDA_* if needed
docker compose up --build
```

### Useful commands

| Command | Purpose |
|---------|---------|
| `docker compose up --build` | Build & run |
| `docker compose down` | Stop |
| `docker compose logs -f mayi` | Follow logs |
| `npm run docker:build` | Image only |

## Deploy on Render

1. Push this repo to GitHub/GitLab.
2. In [Render](https://render.com): **New → Blueprint** and select the repo (uses `render.yaml`),  
   **or** **New → Web Service** → Docker → root `Dockerfile`.
3. Plan: **Starter** minimum; **Standard** recommended if you enable auto-scan + ML.
4. Health check path: `/api/health` (already set).
5. Optional: set secret env vars (`TELEGRAM_BOT_TOKEN`, `OANDA_*`) in the dashboard.
6. Disk mount for `/app/data/store` is declared in `render.yaml` so candle cache survives deploys.

After deploy, open the service URL — the desk is served from the same origin as `/api/*`.

## Local dev (two processes)

```bash
# Terminal 1 — API
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — desk (proxies /api → :8000)
npm install && npm run dev
# http://localhost:8080
```

If the API is down, the desk falls back to synthetic demo data automatically.

## Architecture

| Layer | Location | Role |
|-------|----------|------|
| Desk UI | `src/desk/` | Charts, triage, watchlist, deep-dive, Scan / kill |
| API client | `src/desk/api.ts` | Live FastAPI + offline fallback |
| Backend | `backend/` | yfinance, patterns, context, ML learner, delivery, execution |
| Sister opinion | `ml.ts` + `POST /api/signals/{id}/second-opinion` | CONFIRM / CAUTION / VETO |

## Environment (container)

| Variable | Default | Notes |
|----------|---------|-------|
| `PORT` | `8000` | Listen port (Render sets this) |
| `AUTO_SCAN_ENABLED` | `true` | Hourly engine loop |
| `AUTO_SCAN_INTERVAL_SEC` | `3600` | Min 30 |
| `AUTO_SCAN_ON_START` | `false` | One scan at boot |
| `EXECUTE_ENABLED` | `false` | Real broker orders off |
| `DATA_SOURCE` | `yfinance` | Candle source |

## Image design

Multi-stage production build:

1. **Node 22** — `vite build` → static SPA  
2. **Python 3.11 builder** — install deps from frozen requirements  
3. **Slim runtime** — FastAPI + SPA under `frontend/dist`, non-root user, healthcheck  

```
docker build -t mayi-sentinel:latest .
docker run --rm -p 8000:8000 mayi-sentinel:latest
```
