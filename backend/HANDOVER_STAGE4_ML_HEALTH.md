# Stage 4 — ML Health Manager + UI polish

**Date:** 2026-09-11  
**Scope:** Automated over/underfitting control for the outcome model + first UI spacing/Glossary improvements.

---

## What was delivered

### 1. ML Health Manager (`engine/ml/learner.py`)

Dedicated health state machine evaluated after every successful fit and on load:

| State | Trigger | Automatic response |
|-------|---------|--------------------|
| `insufficient_data` | < min closed trades / no model | Collect more labels |
| `healthy` | Balanced train vs walk-forward | Normal auto-retrain |
| `overfitting` | train_acc − walk_forward ≥ `ML_OVERFIT_GAP` (default 0.18) | **Pause** auto-retrain, switch to **regularized** hyper-params (shallower trees), enable **explore** mode |
| `underfitting` | Both accuracies < `ML_UNDERFIT_ACC` (default 0.52) | **Aggressive** hyper-params, **explore** mode, lower bar for new labels |
| `stale` | Last fit older than `ML_STALE_HOURS` (default 48h) | Allow retrain on next opportunity |

Controls exposed in `/api/ml/status` under `health`:

```json
{
  "state": "overfitting",
  "learning_paused": true,
  "explore_mode": true,
  "hyper_mode": "regularized",
  "detail": {
    "recommendation": "...",
    "metrics": { "gap": 0.188, "train_accuracy": 1.0, "walk_forward_accuracy": 0.812, ... }
  }
}
```

Manual **Retrain now** always bypasses the pause.

Env knobs (also in `config.example.env`):

- `ML_OVERFIT_GAP`, `ML_UNDERFIT_ACC`, `ML_STALE_HOURS`, `ML_MIN_DIVERSITY_PATTERNS`

### 2. ML Insights page

Shows health state badge, gap, walk-forward accuracy, learning paused / explore / hyper-mode controls, and the manager’s recommendation.

### 3. UI polish (first pass)

- Tab bar: more horizontal + vertical spacing, larger hit targets, active ring — less clustered.
- Glossary: parsed into premium term cards (grid) instead of a raw `<pre>` dump.

---

## How to run (Docker)

```bash
docker compose up --build
# open http://localhost:8000
# ML health: curl -s http://localhost:8000/api/ml/status | jq .health
```

Local:

```bash
PYTHONPATH=. python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
cd frontend && npm i && npm run dev
```

---

## Constraints preserved

- No changes to trading/risk logic or execution defaults.
- API contracts extended (additive `health` block only).
- Existing auto-retrain loop still works; health manager only *gates* it.

---

## Suggested next

1. Surface health badge in the sticky header (next to ML chip).
2. Further page-level spacing / density pass on Live Command + Signals.
3. Feature-importance chart on ML Insights when trained.

---

## UI pass 2 (same Stage 4)

- **Header**: ML health chip (Healthy / Overfit / Underfit / Stale) with click-through to ML Insights; shows “paused” when learning is frozen. ML status is polled on every refresh, not only when the ML tab is open.
- **Signals**: Larger filter controls, more grid gap, clearer page header.
- **Live Command**: Slightly more KPI / noise card spacing.
- **Execution**: Consistent page header + gap-8 section rhythm.
- **Glossary** (pass 1): Card grid of terms.
- **Tabs** (pass 1): More horizontal/vertical spacing and active ring.


---

## UI pass 3

- **Live Tape**: Larger row padding, clearer type hierarchy, roomier header/controls, taller feed viewport.
- **Backtest**: Elevated chart cards, CartesianGrid, colour-coded bars, new **Win rate by pair** chart, consistent page spacing and table rhythm.
- **ML Insights**: Horizontal **feature importance** bar chart (top 12 drivers) next to the model snapshot.

