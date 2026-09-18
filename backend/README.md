# Forex Candlestick Signal App

## UI upgrade (TradingView-style React desk)

Primary UI is the React Live Command desk:

- Dark TV-style theme, watchlist + triage + deep-dive
- Real OHLC candlesticks via `GET /api/candles/{pair}?timeframe=1h`
- Multi-TF grid (15m / 1h / 4h / 1d)
- Drawing tools: Entry / SL / TP reference lines (Levels toggle)
- ML health, kill-switch, Scan now, engine tape, order blotter

```bash
cd frontend && npm i && npm run dev   # :5173 proxies /api → :8000
# or build for Docker static serve:
cd frontend && npm i && npm run build
docker compose up --build            # http://localhost:8000
```
## React Live Command (primary UI)

Docker now serves **FastAPI + the React desk** on one port (`8000` by default).
You do **not** need to run the engine before opening the UI — use **Scan now**
in the header. That runs `delivery/runner.py`, appends to `delivery/logs/engine.log`,
refreshes signals, and rebuilds multi-TF / structure / zone / COT bias cards.

```bash
docker compose up --build
# open http://localhost:8000
```

See **[DOCKER.md](DOCKER.md)** for image build, registry push, Makefile targets, and cloud deploy notes.

Legacy Streamlit: `docker compose --profile streamlit up` (port 8501).

Local dev (API + Vite):
```bash
./start-stage2.sh          # API :8000
cd frontend && npm i && npm run dev   # Vite :5173
```


Alerts-only forex signal generator with **optional** broker execution:
detects candlestick patterns, filters by trend / S-R / volatility, computes
entry/SL/TP, delivers Telegram alerts, logs everything, and can place
OANDA market orders (dry-run by default).

## Stack

- **Python** — pandas / numpy scoring engine; Streamlit dashboard
- **yfinance** for data prototyping (swap for OANDA/Twelve Data later)
- **JSONL** signal log (`delivery/logs/signals.jsonl`)
- **Telegram** for push alerts (optional)
- **OANDA v20** for optional Stage 7 execution (practice or live)

## Project layout

```
data/        price data ingestion + instrument registry (Stage 1)
engine/      patterns + context filters + trade params (Stages 2–4)
backtest/    real backtest, walk-forward, sweeps, Monte Carlo (Stage 4)
delivery/    plain-language alerts + signal log (Stage 5)
dashboard/   Streamlit review UI with glossary (Stage 6)
execution/   broker adapters + risk + executor (Stage 7)
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.env .env
```

## Quick start

### Replay / smoke test (Stages 5–6 data)

```bash
python delivery/test_delivery.py
python delivery/runner.py --mode replay --pair EURUSD=X --timeframe 1h --alert
streamlit run dashboard/app.py
```

### Stage 7 — dry-run execution (safe)

```bash
python execution/test_execution.py
python delivery/runner.py --mode replay --execute
# live loop, still dry-run:
python delivery/runner.py --mode live --execute
```

Orders are printed and written to `execution/logs/orders.jsonl`. Nothing
is sent to a broker.

### Stage 7 — real OANDA orders (practice first)

1. Create a **practice** account at OANDA, generate an API token.
2. Put credentials in `.env`:

```
OANDA_API_KEY=...
OANDA_ACCOUNT_ID=...
OANDA_ENV=practice
EXECUTE_ENABLED=true
```

3. Run with both flags:

```bash
python delivery/runner.py --mode live --execute --live-broker
```

`--live-broker` alone is not enough — `EXECUTE_ENABLED=true` is also required.
Without either, the system stays in dry-run.

## Risk controls (Stage 7)

| Env var | Default | Meaning |
|---------|---------|---------|
| `EXECUTE_ENABLED` | `false` | Master switch for real broker |
| `EXEC_KILL_SWITCH` | `false` | Emergency stop — blocks all new orders |
| `RISK_PER_TRADE` | `0.01` | Fraction of equity risked per trade |
| `MAX_UNITS` | `1000` | Hard cap on OANDA units (~0.01 lot) |
| `EXEC_MIN_SCORE` | `50` | Minimum \|final_score\| to execute |
| `MAX_OPEN_POSITIONS` | `2` | Concurrent positions |
| `MAX_DAILY_TRADES` | `5` | New trades per UTC day |

Position size ≈ `(equity × risk_per_trade) / |entry − SL|`, capped by `MAX_UNITS`.

## Context filters

`engine/context.py` takes each raw pattern score and multiplies it by how
much the surrounding market context supports it:

| Filter | What it checks |
|---|---|
| Trend | Price vs. an EMA (+ slope) — long trend (HTF or same-timeframe) and a short-term trend used only for early counter-trend fades |
| Support/resistance | Proximity to a recent swing high/low or a round-number level |
| Market structure | Break of Structure (BOS, continuation) / Change of Character (CHoCH, first sign of reversal) against confirmed swing points — more responsive than the MA trend, which lags |
| Supply/demand zones | Is price inside the base candle that preceded a strong impulsive move? Untested ("fresh") zones score higher than already-retested ones; sitting inside the *opposing* zone is a headwind |
| Volatility | Candle range vs. ATR — spikes (likely news) are discounted, dead/quiet candles get a small penalty too |

Each filter is a multiplier, so a pattern with no context support decays
toward 0 while one with several filters agreeing gets boosted (capped at
±100). Per-pattern diagnostic columns (`_trend`, `_regime`, `_sr`,
`_structure`, `_zone`, `_vol`) are all logged in `meta` alongside every
signal — see `delivery/runner.py` — so they're available for backtesting
and the ML layer without needing to touch the engine again.

## Signal pipeline

```
candles → detect_patterns
       → apply_context_filters (trend × S/R × structure × zones × volatility)
       → threshold (|score| ≥ 40 for alert, ≥ EXEC_MIN_SCORE for trade)
       → compute_trade_levels (ATR/swing SL, fixed R:R TP)
       → log + Telegram alert
       → [optional] risk check → OANDA / dry-run order
```

## Status

- [x] Stage 0 — project skeleton
- [x] Stage 1 — data ingestion
- [x] Stage 2 — candlestick patterns
- [x] Stage 3 — context filters (trend, S/R, market structure, supply/demand zones, volatility)
- [x] Stage 4 — trade parameters + outcome simulation
- [x] Stage 5 — live delivery + signal log
- [x] Stage 6 — dashboard
- [x] Stage 7 — broker execution (OANDA + dry-run, risk-gated)

## Safety

- Default is **never** to place real orders.
- Real execution requires `EXECUTE_ENABLED=true` **and** `--live-broker`.
- Prefer `OANDA_ENV=practice` until you have proven the system on demo.
- This is not financial advice; you are responsible for any live trading.


## Instrument registry & scale-aware engine

All markets live in `data/instruments.py` (ticker ↔ OANDA code ↔ pip size ↔
round-number spacing ↔ tweezer tolerance). Pattern and S/R filters read those
values so JPY pairs and indices are not scored with EUR/USD-tuned constants.

Default watch list:

| Ticker | OANDA | Class |
|--------|-------|-------|
| EURUSD=X | EUR_USD | FX major |
| GBPUSD=X | GBP_USD | FX major |
| USDJPY=X | USD_JPY | FX (diversifier) |
| GBPJPY=X | GBP_JPY | FX volatile |
| ^DJI | US30_USD | Index |

Sentiment remains live-only and is skipped gracefully for indices (no IG market id).

## Backtesting

```bash
python backtest/run_backtest.py
python backtest/run_backtest.py --sweep --walk-forward --monte-carlo
```

Reports land in `backtest/reports/` (comparison.csv, per_pattern.csv,
score_distribution.json, optional sweep/walk-forward/monte-carlo files).

**Data depth note:** 1h and 4h history via yfinance is limited to roughly 60
days (4h is resampled from 1h). Multi-year 4h history needs OANDA or Twelve
Data, not yfinance.

## Plain-language alerts & Live Command Center

Telegram alerts and the Streamlit dashboard lead with Buy/Sell opportunity,
Weak/Moderate/Strong confidence (from backtest score quantiles when available),
max-loss / target wording, and a short “why this fired” sentence. Raw columns
stay available under **Show advanced (raw) table**.

### Live Command Center (`streamlit run dashboard/app.py`)

Full web desk with six tabs:

| Tab | What it does |
|-----|----------------|
| **🎯 Live Command** | Auto-scanning watch list (EUR/USD, GBP/USD, USD/JPY, GBP/JPY, US30), Act now / Watch triage, directional bias, closed-trade R curve. Queue dry-run from any open card. |
| **📋 Signals** | Plain-language cards with entry, max-loss, target + optional raw table |
| **📊 Backtest** | Reports from `backtest/reports/` — win rates, expectancy (R), per-pattern, Monte Carlo snippets |
| **📖 Glossary** | pip, stop, target, confidence, triage rules |
| **📜 Engine log** | Live tail of `delivery/logs/engine.log` |
| **⚙️ Execution** | Kill switch, score/daily/position caps, dry-run blotter (no live broker) |

**Sidebar controls**

- **Pause / Resume** auto-refresh
- **Refresh interval** slider (0 = manual, recommended 5–10 s when active)
- **Scan now** — one-shot replay pass over the default watch list
- Filters (market, confidence, noise, date range)
- **Kill switch** toggle (writes `execution/logs/kill_switch.flag`)

**Priority triage**

- 🔴 **Act now** — |score| ≥ 70, still open, bar ≤ 6 h old  
- 🟡 **Watch** — |score| ≥ 50 or recently closed  
- ⚪ **Noise** — weak / old / already resolved (hidden by default)

**Safety**

- Nothing in the dashboard places a real order.
- **Queue dry-run** on a card writes only to `execution/logs/orders.jsonl`.
- Real OANDA orders still require `EXECUTE_ENABLED=true` **and** `--live-broker`.

---

## Stage 1 update (2026-09-07) — React desk scaffold

A React + TypeScript + Vite + Tailwind command-desk frontend has been scaffolded under `frontend/`.

- All original Python dependencies are installed in `venv/` (recreate if needed).
- React dependencies are listed in `frontend/package.json` (run `npm install` after unpacking).
- A professional dark trading-desk shell with the six target tabs is present as a placeholder.
- Full React UI + FastAPI bridge + ML learning layer = **Stage 2** (see `HANDOVER_STAGE1.md`).

### Quick React start
```bash
cd frontend
npm install --registry https://registry.npmjs.org/
npm run dev
```

Legacy Streamlit dashboard remains fully operational:
```bash
streamlit run dashboard/app.py
```

---

## Stage 2 (2026-09-07) — React desk + FastAPI + ML

See **HANDOVER_STAGE2.md** for full details.

Quick start:
```bash
# API
PYTHONPATH=. python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# React
cd frontend && npm install && npm run dev
```
