# =============================================================================
# Ma-yi Sentinel — premium production image
# Single port: FastAPI (API + engines + ML) + React Live Command desk
#
# Build:  docker build -t mayi-sentinel:latest .
# Run:    docker compose up --build
# Render: connected via render.yaml
# =============================================================================

# ---------- Stage 1: build React desk (static SPA) ----------
FROM node:22-bookworm-slim AS frontend
WORKDIR /fe

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src

ENV VITE_API_BASE=
RUN npm run build

# ---------- Stage 2: Python deps ----------
FROM python:3.11-slim-bookworm AS builder
WORKDIR /build

COPY backend/requirements-frozen.txt backend/requirements.txt ./
RUN pip install --prefix=/install --no-cache-dir -r requirements-frozen.txt \
    || pip install --prefix=/install --no-cache-dir -r requirements.txt

# ---------- Stage 3: runtime ----------
FROM python:3.11-slim-bookworm
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash mayi

# Python packages
COPY --from=builder /install /usr/local

# Backend application
COPY backend/ ./

# Production desk SPA → path FastAPI already serves
COPY --from=frontend /fe/dist ./frontend/dist

# Entrypoint
COPY docker/start.sh /app/start-docker.sh
RUN chmod +x /app/start-docker.sh \
    && mkdir -p delivery/logs execution/logs data/store backtest/reports frontend/dist \
    && chown -R mayi:mayi /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/delivery:/app/data:/app/engine:/app/execution \
    PORT=8000 \
    DATA_SOURCE=yfinance \
    RUNNER_MODE=replay \
    AUTO_SCAN_ENABLED=true \
    AUTO_SCAN_INTERVAL_SEC=3600 \
    AUTO_SCAN_MODE=replay \
    AUTO_SCAN_TIMEFRAME=1h \
    AUTO_SCAN_PERIOD=5d \
    AUTO_SCAN_ON_START=false \
    EXECUTE_ENABLED=false \
    EXEC_KILL_SWITCH=false

USER mayi
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

CMD ["/app/start-docker.sh"]
