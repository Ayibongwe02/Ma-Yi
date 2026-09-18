# Ma-yi Live Command Center — Stage 1 Handover Prompt

**Date:** 2026-09-07  
**Focus completed:** Dependencies downloaded + React desk scaffold + repackage  
**Next agent / Stage 2:** Continue from here.

---

## What was done in Stage 1 (this package)

1. **Python backend dependencies installed** (venv created & frozen):
   - pandas, numpy, yfinance, ta, matplotlib, python-dotenv, requests, streamlit
   - Virtualenv at `Ma-yi-master/venv/` (do **not** commit the full venv to git; recreate with `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`)

2. **React + TypeScript desk scaffold created** under `frontend/`:
   - Vite + React 19 + TypeScript
   - Tailwind CSS v4
   - Recharts, Lucide icons, TanStack Query, Axios, date-fns, clsx
   - Ready for a professional trading command-center UI that will replace the current Streamlit `dashboard/app.py`

3. **Original Python engine, data, backtest, delivery, execution layers left intact**  
   Data sources (yfinance + optional OANDA) are **unchanged**.  
   All pattern / context / confidence / trade-param logic remains the source of truth.

---

## Stage 2 goals (handover prompt for next session)

Rebuild the **Live Command Center as a full React desk** (desktop-first, dark professional trading UI) while keeping the Python engine as the backend.

### Required Stage 2 deliverables

1. **React desk UI** that mirrors + improves the current Streamlit tabs:
   - Live Command (watch-list strip, Act Now / Watch triage, bias KPIs, R-curve)
   - Signals (plain-language cards with entry / max-loss / target)
   - Backtest (win-rate tables, per-pattern, Monte Carlo, score distribution)
   - Glossary
   - Engine log (live tape)
   - Execution (kill-switch, risk caps, dry-run blotter)

2. **Backend bridge** (recommended FastAPI or keep Streamlit as API temporarily):
   - Expose endpoints for signals, engine log, backtest reports, orders.jsonl, kill-switch, scan-now, queue dry-run
   - Keep existing `delivery/runner.py` and `engine/*` as the single source of truth

3. **ML / Deep-Learning improvements to win rate** (data sources stay the same):
   - Add a learning layer that records every signal + eventual outcome
   - Train lightweight models (e.g. gradient boosting or small neural nets) on historical pattern + context features → outcome
   - Detect anomalies and “low-confidence but high-hit-rate” setups
   - Online / incremental learning so the system can apply what it learns
   - Feature ideas: pattern type, score components, HTF trend alignment, S/R proximity, volatility regime, time-of-day, instrument, consecutive wins/losses
   - Goal: raise expectancy / win rate without changing the candle or sentiment data feeds

4. **Repackage** the full application (Python + React build + docs) as a clean zip with clear README and start scripts.

---

## How to continue (copy-paste prompt for Stage 2)

```
Continue from Stage 1 of Ma-yi Live Command Center rebuild.

The package already has:
- Python engine + Streamlit dashboard (legacy)
- venv with all original deps
- frontend/ React+TS+Vite+Tailwind+Recharts scaffold with node_modules

Tasks for Stage 2:
1. Build a professional React trading desk UI that replaces the Streamlit dashboard.
2. Add a thin FastAPI (or similar) bridge so the React desk can call the existing Python engine, signals, backtest reports and execution logs.
3. Implement smarter ML / deep-learning layer on top of the existing pattern+context engine:
   - learn from past signals → outcomes
   - detect anomalies and high-value low-confidence setups
   - improve win rate / expectancy without changing data sources (yfinance / OANDA)
4. Keep dry-run / kill-switch / risk controls intact.
5. Final deliverable: fully working React desk + improved engine + clean repackaged zip + updated handover notes.
```

---

## Quick start after unpacking this Stage 1 package

```bash
cd Ma-yi-master

# Python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config.example.env .env

# React
cd frontend
npm install --registry https://registry.npmjs.org/
npm run dev          # → http://localhost:5173

# Legacy Streamlit still works
streamlit run dashboard/app.py
```

**Do not delete the Streamlit dashboard until the React desk is feature-complete and the API bridge is solid.**

