#!/usr/bin/env bash
set -euo pipefail
cd /app

export PYTHONPATH="${PYTHONPATH:-/app}:/app:/app/delivery:/app/data:/app/engine:/app/execution"
PORT="${PORT:-8000}"

mkdir -p delivery/logs execution/logs data/store backtest/reports

echo "[mayi] Ma-yi Sentinel starting on 0.0.0.0:${PORT}"
echo "[mayi] SPA present: $( [ -f frontend/dist/index.html ] && echo yes || echo NO )"
echo "[mayi] AUTO_SCAN_ENABLED=${AUTO_SCAN_ENABLED:-true} interval=${AUTO_SCAN_INTERVAL_SEC:-3600}s"

exec python -m uvicorn api.main:app --host 0.0.0.0 --port "${PORT}" --proxy-headers --forwarded-allow-ips='*'
