# Ma-yi Live Command Center — Stage 2 Complete

**Date:** 2026-09-07

## Delivered

### 1. React trading desk (`frontend/`)
- Dark professional desk UI with 7 tabs: Live Command, Signals, Backtest, ML Insights, Glossary, Engine Log, Execution
- KPIs, Act-now / Watch triage, signal cards with ML badges & anomaly flags
- Kill switch, Scan now, pause/resume, auto-refresh
- Wired to FastAPI via axios (`src/lib/api.ts`)

### 2. FastAPI bridge (`api/main.py`)
- `/api/health`, `/api/live-command`, `/api/signals`, `/api/stats`
- `/api/backtest/*` (comparison, per-pattern, trades, score-distribution)
- `/api/engine-log`, `/api/orders`, `/api/kill-switch`
- `/api/scan` (one-shot runner), `/api/glossary`
- `/api/ml/status`, `/api/ml/retrain`, `/api/ml/insights`
- CORS open for local React dev

### 3. ML learning layer (`engine/ml/learner.py`)
- GradientBoostingClassifier on closed signal outcomes
- Features: abs/raw score, direction, trend, S/R, volatility regime, RR, hour, major flag, pattern id
- Detects anomalies: low engine confidence + high historical hit-rate (and inverse)
- Produces `ml_prob_win`, `ml_adjusted_score`, `ml_anomaly`, `ml_note` on every signal
- Online retrain via API; model persisted to `engine/ml/model.joblib`
- **Data sources unchanged** (still yfinance / OANDA)

### 4. Seeded data
- 20 sample closed signals (70% win rate) for immediate UI + ML demo

## How to run

```bash
cd Ma-yi-master

# Python deps
python3 -m pip install -r requirements.txt
# or: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt

# API (port 8000)
PYTHONPATH=. python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# React desk (port 5173)
cd frontend
npm install --registry https://registry.npmjs.org/
npm run dev
```

Legacy Streamlit still works: `streamlit run dashboard/app.py`

## Constraints preserved
- Sentiment still live-only
- Execution dry-run unless EXECUTE_ENABLED + --live-broker
- No change to candle / sentiment data feeds

## Suggested Stage 3
- Wire real live scan loop into the desk
- Expand ML with more features / walk-forward validation
- Optional WebSocket for live tape
- Docker compose for API + frontend
