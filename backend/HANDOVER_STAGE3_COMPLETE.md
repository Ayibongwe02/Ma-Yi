# Ma-yi Live Command Center — Stage 3 Complete

**Date:** 2026-09-08  
**Scope:** Full premium React frontend rebuild (design system + all 8 tabs) + Docker packaging

---

## Delivered

### Design system (Part 1)
- Dark-first, dense trading-terminal tokens in `frontend/src/index.css` (`@theme`)
- Tailwind v4 via `@tailwindcss/vite`
- Shared components: `Card`, `Badge`/`StatusChip`, `KpiStat`, `DataTable`, `LiveIndicator`, `ButtonKillSwitch`, `SignalCard`
- Sticky shell: scan / pause / kill-switch (confirm-to-toggle), status chrome, tab nav
- Tabular numerics (JetBrains Mono) + Inter labels

### All 8 pages rebuilt (Part 2)

| Page | File | Highlights |
|------|------|------------|
| Live Command | `pages/LiveCommand.tsx` | KPI strip, bias grid, Act Now / Watch / Noise triage |
| Signals | `pages/Signals.tsx` | Sort/filter (score, time, pair, ML; direction; outcome; anomalies) |
| Live Tape | `pages/LiveTape.tsx` | Auto-scroll feed, pause-on-hover / pause button, clear |
| Backtest | `pages/Backtest.tsx` | Recharts: per-pattern win-rate + score distribution; DataTables |
| ML Insights | `pages/MLInsights.tsx` | Model health panel (samples, accuracy, cooldown, status); insights cards |
| Glossary | `pages/Glossary.tsx` | Token-matched card layout |
| Engine Log | `pages/EngineLog.tsx` | Monospace viewer with basic level coloring (error/warn/info) |
| Execution | `pages/Execution.tsx` | Risk panel + blotter; kill state + dry-run badge also in header |

### Header chrome
- Kill switch always visible (confirm-to-toggle)
- Dry-run mode badge in brand row
- Live indicators: API, ML, Tape, KILL ACTIVE
- Responsive tab labels (short on narrow viewports)

### Constraints preserved
- No changes to `engine/`, `execution/`, `delivery/`, `data/`, `backtest/`, or `api/main.py` contracts
- No `.env` / risk defaults changed (`EXECUTE_ENABLED=false`, etc.)
- Single-port Docker model (FastAPI serves SPA + `/api/*` on 8000)

---

## How to run

```bash
cd Ma-yi-master
docker compose up --build
# App: http://localhost:8000
# Health: curl -f http://localhost:8000/api/health
```

Local dev (optional):
```bash
# Terminal 1
PYTHONPATH=. python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
# Terminal 2
cd frontend && npm install && npm run dev
```

---

## File map (frontend)

```
frontend/
  vite.config.ts              # react + @tailwindcss/vite
  index.html                  # Inter + JetBrains Mono
  src/
    index.css                 # Design tokens
    App.tsx                   # Shell + tab routing
    App.css                   # Legacy CSS retained (minimal use)
    components/
      Card.tsx Badge.tsx KpiStat.tsx LiveIndicator.tsx
      ButtonKillSwitch.tsx DataTable.tsx SignalCard.tsx index.ts
    pages/
      LiveCommand.tsx Signals.tsx LiveTape.tsx Backtest.tsx
      MLInsights.tsx Glossary.tsx EngineLog.tsx Execution.tsx
    lib/
      api.ts utils.ts
```

---

## Acceptance checklist

- [x] No trading/risk logic or API contract changes
- [x] Numeric data uses tabular figures
- [x] Kill-switch and dry-run state unambiguous in header
- [x] All 8 tabs use design system
- [x] Recharts on Backtest (score dist + pattern win-rate)
- [x] Live Tape auto-scroll with pause-on-hover
- [x] Tablet-friendly breakpoints (sm/md) without abandoning desktop density
- [ ] Docker: run `docker compose up --build` and confirm health + UI (operator step)

---

## Suggested Stage 4

1. **Live scan loop** — optional background interval (env-gated) so Scan is not the only path
2. **Walk-forward / richer ML** — more features, calibration plot, feature importance chart
3. **Pair drill-down** — click bias card or signal → focused chart + recent bars (lightweight)
4. **Notification preferences** — toast on Act Now / kill-switch / anomaly
5. **Auth / multi-desk** — if multi-user becomes a requirement
6. **Performance** — virtualize tape + signals lists if volume grows

---

**Stage 3 complete.** Hand off for Docker verification and Stage 4 planning.
