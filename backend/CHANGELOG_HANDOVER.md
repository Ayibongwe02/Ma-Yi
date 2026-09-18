# Ma-yi handover update (2026-09)

## What changed

### Stage 3.5 — Institutional positioning (CFTC COT)
- `data/cot.py` — free, no-key CFTC "Commitment of Traders" (Legacy Futures
  Only) ingestion. Weekly large-speculator net positioning per currency,
  turned into a 0-100 "COT Index" (percentile within its own trailing
  ~3-year range) then rescaled to -100..+100. Directional, not contrarian
  (unlike IG sentiment) — see `engine/STAGE3_5_COT_DELIVERABLE.md`.
- `data/instruments.py` — new `cot_legs` field mapping each pair to its
  CFTC currency leg(s) + sign (crosses average two legs; indices get
  `None`, same pattern as `ig_market_id`).
- `engine/confidence.py` — `blend_with_cot`, `retail_institutional_divergence`,
  and `combine_signals` (pattern + sentiment + COT, with a confluence
  bonus/penalty when retail and institutions agree/disagree). Backward
  compatible: reduces to the old sentiment-only blend when COT is absent.
- `delivery/runner.py` — live mode now also fetches a COT score
  (`_live_cot_score`) and blends it in via `combine_signals`, same
  live-bar-only rule as sentiment (never applied to replay/backtest bars).
- `config.example.env` — `COT_WEIGHT`, `COT_DIVERGENCE_BONUS`.
- Tests: `data/test_cot.py`, extended `engine/test_confidence.py`.

### Phase 4 — Live Command Center (dashboard rebuild)
- `dashboard/app.py` rebuilt as a full web command center with six tabs:
  Live Command, Signals, Backtest, Glossary, Engine log, Execution.
- Sidebar: Pause/Resume, Scan now (one-shot live pass), refresh interval,
  kill-switch toggle, filters.
- Live Command: watch-list strip, Act now / Watch triage, bias KPIs,
  closed-trade R curve, **Queue dry-run** buttons on open cards.
- Backtest tab surfaces comparison.csv, per_pattern.csv, Monte Carlo,
  score distribution and trade lists from `backtest/reports/`.
- Execution tab: risk/score/daily caps snapshot + dry-run blotter
  (`execution/logs/orders.jsonl`). Nothing places a real order.

### Phase 1 — Instrument registry + scale-aware engine
- `data/instruments.py` — single registry for ticker, OANDA code, pip size,
  round-number spacing, tweezer tolerance, asset class, IG sentiment id.
- Instruments: EURUSD=X, GBPUSD=X, USDJPY=X, GBPJPY=X, ^DJI (US30_USD).
- `engine/patterns.py` — tweezer tolerance is per-instrument.
- `delivery/runner.py` — passes scale params into patterns + context + trade levels.
- `execution/broker.py` — registry-based OANDA mapping (indices work).
- `data/sentiment.py` — registry-aware; indices skip sentiment gracefully.
- `config.example.env` — five-instrument default PAIRS list.

### Phase 2 — Real backtest
- `backtest/engine.py` — pipeline backtest (no live sentiment on history),
  per-pattern breakdown, walk-forward, parameter sweep, Monte Carlo DD/ruin.
- `backtest/run_backtest.py` — CLI across all instruments.
- Reports under `backtest/reports/` when you run the CLI.

### Phase 3 — Plain-language UI
- `delivery/plain_language.py` — narrative alerts, Weak/Moderate/Strong bands,
  glossary text.
- `delivery/alerts.py` — default plain-English Telegram/console messages.
- `dashboard/app.py` — plain-language cards + Glossary tab + optional raw table.

## Constraints preserved
- Sentiment blending remains live-only.
- Execution stays dry-run unless EXECUTE_ENABLED=true and --live-broker.
- EUR/USD and GBP/USD flows still work; scale defaults match prior majors.

## How to verify
```bash
pip install -r requirements.txt
cp config.example.env .env
python data/test_instruments.py
python execution/test_execution.py
python delivery/test_delivery.py
python backtest/run_backtest.py --monte-carlo
streamlit run dashboard/app.py
```

## Data depth note
yfinance 1h/4h history is ~60 days (4h resampled from 1h). Longer 4h history
needs OANDA or Twelve Data, not yfinance.

## 2026-09-08 — React primary + Scan ownership + Live bias (A then B)

### A — Docker + Scan ownership
- Default Docker image builds the Vite React app and serves it from FastAPI
  (`frontend/dist`) on `$PORT` (8000). Entrypoint: `start-react.sh`.
- No background runner on start. **Scan now** (`POST /api/scan`) owns engine
  runs, writes `delivery/logs/engine.log`, and optionally refreshes pair bias.
- `delivery/runner.py`: `--all-pairs` / omit `--pair` scans env `PAIRS` in replay.
- `docker-compose.yml` mounts `backtest/reports` and `data/store`; Streamlit
  moved to profile `streamlit`.
- Glossary + Backtest tabs show clear empty states when data is missing.

### B — Live Command enrichments
- New `api/pair_bias.py`: multi-TF (HTF vs STF) alignment, market structure
  event, supply/demand zone label, COT score, retail-vs-institutional divergence.
- Cached at `delivery/logs/pair_bias.json`; refreshed after each Scan.
- Live Command shows bias cards; `GET /api/pair-bias`, `POST /api/pair-bias/refresh`.

## 2026-09-08 — Automated ML retrain (fired + labeled)

- `engine/ml/learner.py`: `maybe_retrain()` / `auto_retrain()` with
  min samples, min new labels since last fit, and cooldown.
- Triggers:
  1. After **Scan** (replay simulates win/loss on fired bars)
  2. After **outcome label** (API PATCH or logger.update_outcome)
  3. **Background interval** (default 300s) while API is up
  4. Manual **Retrain** button still forces a fit
- Env: `ML_AUTO_RETRAIN`, `ML_MIN_SAMPLES`, `ML_MIN_NEW_LABELS`,
  `ML_RETRAIN_COOLDOWN_SEC`, `ML_RETRAIN_INTERVAL_SEC`
- ML tab shows auto-retrain status banner

## 2026-09-10 — Sentinel contract
- Added GET /api/signals/top?n=5 (open/live candidates sorted by |final_score|)
- Sister system 'Sentinel' lives in ../Sentinel — independent Confirm/Veto layer


### UI polish — analysis on cards, headline, motion (2026-09-10)
- Live Command signal cards show plain-English **Why** analysis (Sentinel
  rationale when present, else engine-derived narrative).
- Market brief headline banner: simplified English desk read from bias /
  alignment / COT / act-now counts.
- Animated graphic **Ma-yi** logo in the header.
- Refresh spin, top progress bar, page/skeleton loaders on tab switch and
  first load; page enter motion (respects reduced-motion).
