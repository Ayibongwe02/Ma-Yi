# =============================================================================
# Ma-yi Sentinel — production multi-stage image
# Serves FastAPI + React SPA on a single port (default 8000).
#
# Build from this directory (backend/):
#   docker build -t mayi-sentinel:latest .
#   docker compose up --build
# =============================================================================

# ---------- Stage 1: build React frontend ----------
FROM node:22-slim AS frontend
WORKDIR /fe

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY frontend/ ./
# Empty VITE_API_URL → browser uses same origin (FastAPI serves SPA + /api)
ENV VITE_API_URL=
RUN npm run build

# ---------- Stage 2: install Python dependencies ----------
FROM python:3.11-slim AS builder
WORKDIR /build

COPY requirements-frozen.txt requirements.txt ./
RUN pip install --prefix=/install --no-cache-dir -r requirements-frozen.txt \
    || pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---------- Stage 3: lean runtime image ----------
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

# Application source (backend context)
COPY . .

# Overlay production SPA build
COPY --from=frontend /fe/dist /app/frontend/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DATA_SOURCE=yfinance \
    RUNNER_MODE=replay \
    AUTO_SCAN_ENABLED=true \
    AUTO_SCAN_INTERVAL_SEC=3600 \
    AUTO_SCAN_MODE=replay \
    AUTO_SCAN_TIMEFRAME=1h \
    AUTO_SCAN_PERIOD=5d \
    EXECUTE_ENABLED=false \
    ML_MIN_SAMPLES=8 \
    ML_MIN_NEW_LABELS=2 \
    ML_OVERFIT_GAP=0.22 \
    ML_BOOTSTRAP_MAX_RATIO=1.0 \
    ML_BOOTSTRAP_FLOOR=40 \
    ML_AUTO_RETRAIN=true \
    EXEC_KILL_SWITCH=false

RUN mkdir -p delivery/logs execution/logs data/store backtest/reports frontend/dist \
    && chmod +x start.sh start-react.sh start-stage2.sh 2>/dev/null || true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -f "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["bash", "start-react.sh"]
