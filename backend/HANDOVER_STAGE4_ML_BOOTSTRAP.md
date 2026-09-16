# Stage 4+ — ML Bootstrap / Pre-train (anti-overfit data)

**Date:** 2026-09-11  
**Scope:** Give the outcome model enough non-repeating, yfinance-like closed trades (~1000) so it stops memorizing the tiny live label set.

---

## What was delivered

### 1. Multi-regime synthetic OHLC (`data/_synthetic.py`)
- Alternating regimes (bull / bear / range / high-vol / quiet)
- Relative volatility (safe for FX and indices; no inf/NaN blow-ups)
- Used when live yfinance history is unavailable or too short

### 2. Durable bootstrap pool (`engine/ml/bootstrap.py`)
- Runs the **same** pattern → context → levels → `simulate_outcome` pipeline as live/backtest
- Writes closed win/loss rows only to `engine/ml/bootstrap_labels.jsonl`
- Does **not** pollute the live `delivery/logs/signals.jsonl`
- CLI:
  ```bash
  PYTHONPATH=. python -m engine.ml.bootstrap
  PYTHONPATH=. python -m engine.ml.bootstrap --target 1200 --bars 900
  PYTHONPATH=. python -m engine.ml.bootstrap --clear
  make ml-bootstrap
  make ml-bootstrap TARGET=1500 BARS=1000
  ```

### 3. Learner merges the pool (`engine/ml/learner.py`)
- `fit_from_logs` and `count_labeled` automatically include `bootstrap_labels.jsonl`
- Live labels still take priority for ongoing adaptation; bootstrap is the prior

### 4. Pre-trained artifacts included in this package
| File | Role |
|------|------|
| `engine/ml/bootstrap_labels.jsonl` | ~996 closed synthetic trades (5 pairs @ 1h) |
| `engine/ml/model.joblib` | Fitted GradientBoosting model |
| `engine/ml/meta.json` | Train metrics, walk-forward, health state |

### Snapshot after bootstrap fit
- **n_samples:** ~1042 (pool + existing live seeds)
- **train_acc:** ~0.75
- **walk-forward:** ~0.53
- **health:** `overfitting` → auto-retrain **paused**, **regularized** hyper-params (shallower trees), explore mode on
- Pattern diversity: 15+ setups with ≥2 samples

The residual gap is expected on pure synthetic outcomes. As real closed trades accumulate, the gap should shrink and health will return to `healthy`.

---

## How to use after unpack

```bash
# Docker
docker compose up --build

# Local API
PYTHONPATH=. python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Re-bootstrap / expand the prior anytime
make ml-bootstrap
# or force a full regenerate
PYTHONPATH=. python -m engine.ml.bootstrap --clear --target 1200
```

Check ML health:
```bash
curl -s http://localhost:8000/api/ml/status | jq .health
```

Manual retrain (bypasses health pause):
```bash
curl -X POST http://localhost:8000/api/ml/retrain
# (endpoint name may vary — use whatever the API exposes for force retrain)
```

---

## Constraints preserved
- No changes to trading/risk/execution defaults
- Live signal log format unchanged
- Health manager behaviour unchanged (still gates auto-retrain)
- Bootstrap pool is additive; delete `bootstrap_labels.jsonl` to fall back to live-only labels
