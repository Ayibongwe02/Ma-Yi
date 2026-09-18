#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-.}:$(pwd):$(pwd)/delivery:$(pwd)/data:$(pwd)/engine:$(pwd)/execution"
PORT="${PORT:-8000}"
echo "[mayi] starting FastAPI + React SPA on 0.0.0.0:${PORT}"
exec python -m uvicorn api.main:app --host 0.0.0.0 --port "${PORT}"
