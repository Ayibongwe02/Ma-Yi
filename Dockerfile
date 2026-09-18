# Convenience wrapper — delegates to backend multi-stage build.
# Prefer building from backend/:
#   cd backend && docker compose up --build
#
# Or from repo root:
#   docker build -f Dockerfile -t mayi-sentinel:latest .
# (context is still backend contents via the path below)

FROM node:22-slim AS frontend
WORKDIR /fe
COPY backend/frontend/package.json backend/frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
COPY backend/frontend/ ./
ENV VITE_API_URL=
RUN npm run build

FROM python:3.11-slim AS builder
WORKDIR /build
COPY backend/requirements-frozen.txt backend/requirements.txt ./
RUN pip install --prefix=/install --no-cache-dir -r requirements-frozen.txt \
    || pip install --prefix=/install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
COPY backend/ ./
COPY --from=frontend /fe/dist /app/frontend/dist
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 \
    DATA_SOURCE=yfinance RUNNER_MODE=replay AUTO_SCAN_ENABLED=true \
    EXECUTE_ENABLED=false \
    ML_MIN_SAMPLES=8 \
    ML_MIN_NEW_LABELS=2 \
    ML_OVERFIT_GAP=0.22 \
    ML_BOOTSTRAP_MAX_RATIO=1.0 \
    ML_BOOTSTRAP_FLOOR=40 \
    ML_AUTO_RETRAIN=true EXEC_KILL_SWITCH=false
RUN mkdir -p delivery/logs execution/logs data/store backtest/reports \
    && chmod +x start.sh start-react.sh start-stage2.sh 2>/dev/null || true
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -f "http://127.0.0.1:${PORT}/api/health" || exit 1
CMD ["bash", "start-react.sh"]
