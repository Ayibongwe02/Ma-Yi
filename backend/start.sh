#!/usr/bin/env bash
# Legacy Streamlit UI entrypoint
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-.}:$(pwd):$(pwd)/delivery:$(pwd)/data:$(pwd)/engine:$(pwd)/execution"
PORT="${PORT:-8501}"
echo "[mayi] starting Streamlit on 0.0.0.0:${PORT}"
exec streamlit run dashboard/app.py --server.port "${PORT}" --server.address 0.0.0.0 --server.headless true
