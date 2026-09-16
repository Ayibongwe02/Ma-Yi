# Ma-yi Live Command Center — Stage 3 Part 1 Handover

**Date:** 2026-09-08  
**Scope completed:** Design system + app shell + Live Command reference page  
**Status:** STOP — do not begin Part 2 in the same session.

---

## What was built

### 1. Design tokens (`frontend/src/index.css`)
Tailwind v4 CSS-first config via `@import "tailwindcss"` + `@theme { ... }`.

| Token group | CSS variables | Notes |
|-------------|---------------|-------|
| Background layers | `--color-canvas`, `--color-panel`, `--color-panel-hover`, `--color-panel-elevated`, `--color-border`, `--color-border-subtle`, `--color-border-strong` | Dark-first, dense |
| Text | `--color-text`, `--color-text-secondary`, `--color-text-muted`, `--color-text-faint` | |
| Direction / P&L | `--color-long`, `--color-long-soft`, `--color-long-bg`, `--color-long-border`, `--color-short`, … | Green/red reserved |
| Risk / status | `--color-warning`, `--color-danger`, `--color-success`, `--color-info` | Amber/red for risk |
| ML accent | `--color-ml`, `--color-ml-soft`, `--color-ml-bg`, `--color-ml-border` | Indigo |
| Interactive | `--color-accent`, `--color-accent-hover`, `--color-accent-soft`, `--color-accent-bg` | |
| Spacing | `--spacing-0` … `--spacing-8` | Tight scale |
| Radii | `--radius-sm` … `--radius-full` | 4–10px, subtle |
| Fonts | `--font-sans` (Inter), `--font-mono` / `--font-tabular` (JetBrains Mono) | Tabular nums via `.font-tabular` / `[data-numeric]` |
| Type scale | `--text-2xs` … `--text-2xl` | |
| Motion | `--duration-fast` (150ms), `--duration-normal` (200ms), `--ease-out` | Functional only |

Vite plugin: `@tailwindcss/vite` added in `vite.config.ts`.

Fonts loaded from Google Fonts in `index.html` (Inter + JetBrains Mono).

### 2. Shared component library (`frontend/src/components/`)

| Component | File | Purpose |
|-----------|------|---------|
| `Card`, `CardHeader`, `CardTitle` | `Card.tsx` | Panel with optional accent border (long/short/warning/ml) |
| `Badge`, `StatusChip` | `Badge.tsx` | Semantic pills (long/short/ml/warning/danger/…) |
| `KpiStat` | `KpiStat.tsx` | Dense KPI tile with tabular value + tone |
| `LiveIndicator` | `LiveIndicator.tsx` | Pulsing status dot: connected / disconnected / scanning / paused / kill / warning |
| `ButtonKillSwitch` | `ButtonKillSwitch.tsx` | Confirm-to-toggle kill switch (3s confirm window) |
| `DataTable` | `DataTable.tsx` | Generic dense table with numeric columns |

Barrel: `frontend/src/components/index.ts`

### 3. App shell (rebuilt in `App.tsx`)
- Sticky header with:
  - LiveIndicator (global state: kill > scanning > paused > connected)
  - Brand + Stage 3 badge
  - Pause / Refresh / Scan now
  - **ButtonKillSwitch** (confirm-to-arm)
  - Status chrome: API / ML / Tape / KILL ACTIVE
- Tab nav using new tokens (active = accent-bg)
- Error banner
- Content area

### 4. Live Command page (full rebuild)
`frontend/src/pages/LiveCommand.tsx` — wired to real `LiveCommand` API data (no mocks).

- KPI strip (Win rate, Fired, Bias, Act now) via `KpiStat`
- Watchlist chip strip
- Multi-TF / institutional bias grid (`BiasCard` with aligned / COT confirm|conflict badges)
- Triage hierarchy: **Act now** (primary, green) → **Watch** (amber) → **Noise** (if present)
- Signal cards: direction badge, score (tabular + tone), ML badge (anomaly = warning accent), entry/SL/TP, outcome

### 5. Legacy tabs (intentionally untouched markup)
Signals, Live Tape, Backtest, ML Insights, Glossary, Engine Log, Execution still use original `App.css` classes and the legacy `SignalCard` inside `App.tsx`. They continue to function; visual polish is Part 2.

### 6. Constraints preserved
- No changes to `engine/`, `execution/`, `delivery/`, `data/`, `backtest/`, or `api/main.py` route contracts.
- No `.env` / risk defaults changed (`EXECUTE_ENABLED=false`, etc.).
- Single-port Docker model unchanged (FastAPI serves SPA from `frontend/dist` on 8000).

---

## API gaps / notes
- Kill state is fetched via `GET /api/kill-switch` on mount and on Execution tab; also opportunistically from `health.kill_switch` if present.
- No new API fields were required for Part 1.
- `live.noise` is rendered when present (type already had it; previous UI omitted it).

---

## Pages status

| Page | Status |
|------|--------|
| Live Command | **Rebuilt** (reference) |
| Signals | Legacy |
| Live Tape | Legacy |
| Backtest | Legacy |
| ML Insights | Legacy |
| Glossary | Legacy |
| Engine Log | Legacy |
| Execution | Legacy (kill control already in global header) |

---

## Docker
`docker compose up --build` should still serve the app on port 8000. The frontend build runs inside the multi-stage Dockerfile (`npm ci` + `npm run build`). Confirm:

```bash
docker compose up --build
# then: curl -f http://localhost:8000/api/health
# open http://localhost:8000 → Live Command should show the new shell + page
```

---

## Token / component reference for Part 2

**Import pattern:**
```ts
import { Card, Badge, KpiStat, LiveIndicator, ButtonKillSwitch, DataTable } from '../components'
import { cn } from '../lib/utils'
```

**Color usage rules (do not break):**
- Green/red → long/short and win/loss only
- Amber/red → kill-switch, anomalies, risk warnings
- ML → indigo badges only
- Everything else neutral (canvas/panel/text-*)

**Numeric data:** always apply `font-tabular` or `data-numeric` + format consistently (prices to 5 dp, scores as integers, % to 0–1 dp).

---

## Ready-to-paste Part 2 kickoff prompt

```
Continue Ma-yi Live Command Center at Stage 3, Part 2.

Part 1 is complete. Read HANDOVER_STAGE3_PART1.md first — reuse the exact design tokens
and components listed there (Card, Badge, KpiStat, LiveIndicator, ButtonKillSwitch, DataTable).
Do not invent parallel tokens or duplicate components.

Do not modify engine/, execution/, delivery/, data/, backtest/, or api/main.py contracts.
Do not change .env / risk defaults.

Part 2 scope:
1. Rebuild the remaining 7 tabs using the Part 1 design system and components:
   - Signals (sortable/filterable list; distinct ML badge + anomaly treatment)
   - Live Tape (real auto-scroll tape with pause-on-hover)
   - Backtest (Recharts for score distribution + per-pattern win-rate; keep tables where useful)
   - ML Insights (status panel for model health; Recharts if useful)
   - Glossary (match visual system)
   - Engine Log (monospace viewer with basic level coloring if format supports)
   - Execution (blotter; kill-switch already lives in header chrome — keep mode indicator visible)
2. Promote dry-run vs live-broker mode indicator into the global header chrome if not already clear.
3. Responsive pass down to a reasonable tablet breakpoint (desktop remains primary).
4. Full Docker verification: fresh `docker compose up --build`, confirm all 8 tabs work against
   live FastAPI, confirm /api/health healthcheck passes.
5. Repackage full Ma-yi-master/ (exclude node_modules, venv, frontend/dist, __pycache__) as
   Stage 3 complete zip.
6. Write HANDOVER_STAGE3_COMPLETE.md (same format as HANDOVER_STAGE2.md), including a
   "Suggested Stage 4" section.

Stop after the complete handover + zip.
```

---

## File map (new / changed)

```
frontend/
  vite.config.ts          # + @tailwindcss/vite
  index.html              # Inter + JetBrains Mono
  src/
    index.css             # Design tokens (@theme)
    App.tsx               # Shell + LiveCommandPage + legacy tabs
    App.css               # Legacy styles retained for unrebuilt tabs
    components/
      index.ts
      Card.tsx
      Badge.tsx
      KpiStat.tsx
      LiveIndicator.tsx
      ButtonKillSwitch.tsx
      DataTable.tsx
    pages/
      LiveCommand.tsx     # Reference page
```

---

**End of Part 1.** Hand off to a new session with the Part 2 prompt above.
