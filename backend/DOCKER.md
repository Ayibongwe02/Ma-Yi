# Ma-yi — Docker image & deployment

Single-port production image: **FastAPI + React Live Command** on port `8000`.

## Quick start (local)

```bash
# 1. Optional secrets / overrides
cp config.example.env .env
# edit .env if you have Telegram / OANDA / IG keys

# 2. Build & run
docker compose up --build
# or: make up

# 3. Open the desk
open http://localhost:8000
# Health: curl -f http://localhost:8000/api/health
```

Use **Scan now** in the UI to run the signal engine on demand.

**Auto-scan is on by default.** The container reruns the engine every
`AUTO_SCAN_INTERVAL_SEC` (default 3600s / 1 hour), refreshing signals, COT/pair
bias, and triggering ML retraining off the newly labeled outcomes — so the
engines always see current data without anyone clicking a button. It's the
exact same code path as the manual scan button, just on a timer.

| Env var | Default | Purpose |
|---|---|---|
| `AUTO_SCAN_ENABLED` | `true` | Turn the recurring loop on/off |
| `AUTO_SCAN_INTERVAL_SEC` | `3600` | Seconds between reruns (min 30) |
| `AUTO_SCAN_MODE` | `replay` | `replay` or `live` |
| `AUTO_SCAN_TIMEFRAME` | `1h` | Timeframe passed to the runner |
| `AUTO_SCAN_PERIOD` | `5d` | Lookback window per pass |
| `AUTO_SCAN_PAIR` | *(blank = all)* | Restrict to one pair, or scan the whole watchlist |
| `AUTO_SCAN_ON_START` | `false` | Fire one extra pass immediately at boot, on top of the loop |

Set `AUTO_SCAN_ENABLED=false` in `.env` if you want the old manual-only
behavior back. Progress and errors from each auto-run are written to
`delivery/logs/engine.log` and streamed to the UI's live tape, prefixed
`[auto-scan]`. Check current status any time via `/api/health`
(`auto_scan_enabled`, `auto_scan_interval_sec`).

### Makefile shortcuts

| Target        | Action                          |
|---------------|---------------------------------|
| `make build`  | Build image `mayi-command:stage3` |
| `make up`     | Build + run (foreground)        |
| `make up-d`   | Build + run detached            |
| `make logs`   | Follow container logs           |
| `make health` | Check `/api/health`             |
| `make shell`  | Shell into running container    |
| `make down`   | Stop containers                 |

## Image creation (standalone)

```bash
docker build -t mayi-command:stage3 .
docker run --rm -p 8000:8000 \
  -v "$(pwd)/delivery/logs:/app/delivery/logs" \
  -v "$(pwd)/execution/logs:/app/execution/logs" \
  -v "$(pwd)/data/store:/app/data/store" \
  -v "$(pwd)/backtest/reports:/app/backtest/reports" \
  --env-file .env \
  mayi-command:stage3
```

## Push to a registry

```bash
# Example: Docker Hub
docker tag mayi-command:stage3 youruser/mayi-command:stage3
docker push youruser/mayi-command:stage3

# Example: GHCR / private registry
export REGISTRY=ghcr.io/your-org
make push
```

## Cloud / PaaS notes

| Platform   | Notes |
|------------|-------|
| **Render** | Web Service, Docker, port `8000`, set env vars in dashboard. Mount disks for `delivery/logs` if you want persistence. |
| **Railway**| Dockerfile deploy; set `PORT` (platform injects it). |
| **Fly.io** | `fly launch` then `fly deploy`. Use volumes for log dirs. |
| **AWS ECS / Fargate** | Push image to ECR; task definition maps 8000; use EFS or S3 for logs if needed. |
| **Google Cloud Run** | Deploy image; set concurrency; env vars for risk flags. |

Always keep:

- `EXECUTE_ENABLED=false` until you intentionally go live
- `EXEC_KILL_SWITCH` available for emergency stop
- Secrets (`OANDA_API_KEY`, `TELEGRAM_BOT_TOKEN`, etc.) only via env / secret manager — never baked into the image

## Architecture of the image

```
Stage 1 (node:22-slim)  →  npm ci + vite build  →  /fe/dist
Stage 2 (python:3.11)   →  pip install (frozen) →  /install
Stage 3 (python:3.11)   →  copy app + dist + site-packages
                         →  CMD start-react.sh  →  uvicorn api.main:app
```

- React SPA is served by FastAPI from `frontend/dist`
- API under `/api/*`, WebSocket under `/ws`
- Volumes keep logs & cached candle data outside the container

## Legacy Streamlit UI

```bash
docker compose --profile streamlit up
# → http://localhost:8501
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Healthcheck fails | Wait for start-period; check `docker compose logs app` |
| Blank UI | Confirm `frontend/dist` was built (rebuild image) |
| Permission errors on volumes | Ensure host dirs exist: `mkdir -p delivery/logs execution/logs data/store backtest/reports` |
| No signals | Click **Scan now**, or confirm `AUTO_SCAN_ENABLED=true` and wait for the next interval (check `/api/health`) |
| Auto-scan not running | Check `docker compose logs app \| grep auto-scan`; confirm `AUTO_SCAN_ENABLED=true` and `AUTO_SCAN_INTERVAL_SEC` isn't set absurdly high |
| yfinance rate limits | Prefer shorter `PAIRS` list or switch `DATA_SOURCE` when you have broker keys |
