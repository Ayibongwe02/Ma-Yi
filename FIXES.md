# Fixes

## 1. Removed the auto-blocking feature (overfit gate)

### Why it had to go
The gate was circular. Scanning is what produces closed outcomes, and closed
outcomes are the labels the model trains on. Refusing to scan because the model
looked overfit removed the only source of the data that would have fixed it.
The same logic applied to `auto_retrain`: `learning_paused` returned early on an
overfit signal, so the model could never absorb new labels and never recovered.

An earlier round had already de-fanged `POST /api/scan`, but the gate was still
live in two other places, so the trap was still closed.

### What changed
| Location | Before | After |
|---|---|---|
| `api/main.py` `_auto_scan_loop()` | hard-skipped every scheduled scan when `safe_to_rescan=false` | no health gate |
| `desk/store.ts` `scan()` | hard-blocked the Scan button client-side | no health gate |
| `engine/ml/learner.py` `auto_retrain()` | early return while `learning_paused` | no pause; retrains with regularized hyperparameters |
| `engine/ml/learner.py` health | emitted `safe_to_rescan` / `rescan_reason` | both removed |
| `desk/TopBar.tsx`, `desk/StatusBar.tsx`, `desk/types.ts`, `desk/ml.ts` | dead `blocked` / `safeRescan` vars, "Scan blocked" badge | removed |

`learning_paused` is kept as a field for API compatibility but is never set to
true. `ScanBody.force` is accepted and ignored so older clients keep working.

**Overfit handling that remains** — and that actually helps: shallower trees,
stronger penalties (`_hyper_mode = "regularized"`), explore mode, and a higher
new-label threshold per refit. Regularize, don't halt.

## 2. Outdated data

### Backend — the cache never expired
`GET /api/candles` only refetched when the CSV was **missing**:

```python
try:
    df = load_cached(pair, timeframe)   # won whenever the file merely existed
except Exception:
    df = fetch_history(...)             # so: only ever on first run
```

`render.yaml` mounts a persistent 2 GB disk at `/app/data/store`, so once each
pair was written the chart froze at that snapshot permanently.

Now the cache is trusted only while the newest bar is within one timeframe
(plus a weekend allowance for FX — see `_max_candle_age_sec`). Otherwise it
refetches. If the refetch fails, the stale cache is still served rather than
erroring, but it is flagged instead of passed off as current. The response
gained `stale`, `age_sec`, `as_of` and `fetched_at`.

### Frontend — synthetic data shown as live
Three separate places presented demo data as real market data:

- `store.ts` swapped in demo-data signals whenever the backend returned zero
  results, so a quiet backend looked identical to a busy one.
- `ChartPanel.tsx` took the header's % change from `sessionChange()`, which
  always read the demo series — the quoted price and the percentage beside it
  came from different datasets.
- `MiniChart` rendered demo candles unconditionally, so the 15m/4h/1d panes
  never matched the 1h chart above them.

All three now use the real series, and fall back to synthetic candles only when
the backend is genuinely unreachable. The status bar shows a freshness readout
(`updated 12s ago`, amber past 90s, `demo feed · not live data` when offline).

## 3. Other bugs found along the way

- **`ML_BOOTSTRAP_MAX_RATIO=0` did the opposite of what it says.** The guard
  `if len(boot_closed) > boot_cap > 0:` is false when the cap is zero, so
  setting the documented "disable bootstrap" value skipped capping entirely and
  loaded the whole ~1000-row pool. This is the same failure mode as the original
  overfitting complaint. `count_labeled()` handled zero correctly, so the two
  also disagreed about pool size. Fixed in `fit_from_logs`.
- **`GET /api/candles` dropped the `period` query parameter.**
- **Tests would not collect.** `test_confidence.py` and `test_learner.py` use
  bare imports that only resolved if pytest ran from inside the package dir.
  Added `backend/conftest.py`.
- **`test_learner.py` asserted the pre-bootstrap contract** (`n_samples == 20`
  when the answer is now 60). Updated to isolate the bootstrap pool explicitly.
- Added an `npm run typecheck` script — `npm run build` is `vite build` alone,
  which does not typecheck, so type errors were shipping silently.

## 4. Duplicate frontend removed

`src/` and `public/` at the repo root were an older, unbuildable copy of
`backend/frontend/src` and `backend/frontend/public` (no `package.json`, no
`vite.config.ts`). A file-by-file diff confirmed every difference was the
shipping copy being *newer* — `Number()` coercion hardening, NaN guards in
`ChartCanvas`, the `react-resizable-panels` removal — so nothing unique was
lost. `public/` was byte-identical.

Deleting it shrank the built CSS from 41.9 kB to 27.4 kB (gzip 8.0 → 6.2 kB):
Tailwind v4 auto-detects sources from the project root, so it had been
generating utilities for the orphaned tree all along. Every class actually used
by the desk was verified present in the new bundle.

`eslint.config.mjs` moved to `backend/frontend/`, where the TypeScript it lints
actually lives.

## 5. scikit-learn version safety

`requirements-frozen.txt` pinned `scikit-learn==1.6.1` while the shipped
`model.joblib` was fit under 1.9.1, and the Dockerfile prefers the frozen file.
A joblib pickle is only valid under the version that wrote it; sklearn *warns*
rather than errors, and its own docs say results may be invalid. A warning is
too quiet for a model that sizes trades.

- `requirements.txt` now pins `scikit-learn==1.6.1` / `joblib==1.4.2` to match
  `requirements-frozen.txt`, instead of floating `>=1.4`.
- `_save()` records `sklearn_version` in `meta.json`; `_load()` discards the
  model on mismatch, exactly like the existing `feature_version` guard, and
  reports why via `status().model_discard_reason`.
- A `meta.json` with no recorded version is treated as unverifiable and also
  discarded. The shipped model hits this path, so the first boot after upgrade
  retrains rather than loading a pickle of unknown provenance.

`learning_paused` is also no longer restored from `meta.json` — the shipped
`meta.json` has `"learning_paused": true` baked in, which would have resurrected
the removed auto-block on the next boot. Covered by a regression test.

## 6. Silent data-fetch failures reported as success (root cause of "no
   signals firing" / "outdated SL·TP on certain pairs")

### The bug
`replay_recent()` / `live_once()` in `delivery/runner.py` already caught a
failed candle fetch (yfinance unreachable/rate-limited) and printed a line —
but returned normally with `[]`. The runner subprocess therefore always
exited `0`, so `_run_scan()` in `api/main.py` reported `ok: true` regardless
of whether any real data was ever fetched. Reproduced directly: with
`query1/query2.finance.yahoo.com` unreachable, a full scan logged
`Summary: 0 fired signals` with **no error anywhere** in the API response or
`/api/health`.

This is one bug with two symptoms:
- If every pair fails that cycle → no signals ever fire, with nothing to
  diagnose why.
- If failures are intermittent per-pair → the pairs that fail keep their
  last-known entry/SL/TP while every other pair keeps updating, so those
  pairs' levels silently go stale relative to the rest of the board.

### Fix
- `runner.py` now collects `{pair, timeframe, reason}` for every failed
  fetch and prints a `RUNNER_SUMMARY:{...json...}` line (`fired`,
  `pairs_attempted`, `fetch_errors`).
- `_run_scan()` parses that line and returns `data_ok` (true only if the
  process succeeded *and* fetched fresh data for every attempted pair),
  `fetch_errors`, and a plain-English `warning`.
- The auto-scan loop treats a non-empty `warning` as `last_error` even when
  `ok=true`, so `/api/health` → `auto_scan.last_error` now surfaces a live
  data outage instead of showing green indefinitely.

## 7. Signal log had no dedup — same bar re-logged as a new row every cycle

### The bug
Every scan re-walks its whole lookback window (auto-scan hourly, live mode's
120-bar window, overlapping replay periods), so the same historical fired
bar gets re-detected repeatedly. `logger.log_signal()` unconditionally
appended a new row each time, so:
- `delivery/logs/signals.jsonl` grew without bound with duplicate rows for
  the same `(pair, timeframe, bar_time)`.
- `GET /api/signals/top` ranks open/pending signals purely by
  `abs(final_score)`. In live mode `outcome` never resolves (only replay
  simulates win/loss), so an old duplicated pending signal could rank above
  — and be served as — the current top pick for a pair, showing entry/SL/TP
  computed off a candle days old.

### Fix
- `log_signal()` now dedupes on `(pair, timeframe, bar_time)`: a re-detected
  bar updates the existing row in place (fresh score/levels/outcome) instead
  of inserting a duplicate. `alerted` is sticky (never flips back to false).
- `GET /api/signals/top` now drops any pending signal whose bar is older
  than one timeframe-bar (+ the same weekend allowance `/api/candles` uses)
  before ranking by score, so a stale pending signal can no longer outrank a
  fresh one just because its historical score happened to be higher.

## 8. AUTO_SCAN_ENABLED defaulted to off at the code level

### The bug
Every shipped deployment path (root `Dockerfile`, `backend/Dockerfile`,
`docker-compose.yml`, `render.yaml`) explicitly set `AUTO_SCAN_ENABLED=true`
as an env var — but the code's own default, if that env var was ever unset,
was `"false"`. That only worked out by coincidence of always going through
one of those files. Any other way of running the service (bare
`uvicorn api.main:app`, a different PaaS, a systemd unit, a Render service
created without the blueprint) skipped all of them, silently landed on the
code default, and auto-scan just never ran — with nothing in the app itself
to say why.

`AUTO_SCAN_ON_START` had the same shape of problem one level deeper: it was
declared in every deployment config as if it controlled something, but
nothing in `api/main.py` ever read it.

### Fix
- `_auto_scan_enabled()`'s code-level default flipped to `true`. Scanning
  is now on unless an operator explicitly opts out with
  `AUTO_SCAN_ENABLED=false`, regardless of how the process is launched.
- `AUTO_SCAN_ON_START` is now actually read (default `true`: run the first
  pass ~15s after boot instead of waiting a full interval).
- `_auto_scan_loop()` prints the fully-resolved effective config
  (`AUTO_SCAN_ENABLED`, interval, mode, timeframe, period) directly to
  stdout at boot, so it's visible in whatever log stream the process has —
  not something you have to know to go query `/api/health` for.

## Verification
```bash
cd backend && python -m pytest engine delivery data -q    # 40 passed
cd backend/frontend && npm run typecheck && npm run build # clean
```

## Env knobs (unchanged)
| Variable | Default | Meaning |
|----------|---------|---------|
| `ML_BOOTSTRAP_MAX_RATIO` | 1.0 | Max bootstrap/live ratio (0 = live only) |
| `ML_BOOTSTRAP_FLOOR` | 40 | Min bootstrap when live≈0 |
| `ML_MIN_SAMPLES` | 8 | Min rows to fit |
| `ML_OVERFIT_GAP` | 0.22 | Train−WF gap → regularize |
| `ML_MIN_NEW_LABELS` | 2 | New labels before auto-retrain |

## Docker
```bash
cd backend
docker compose up --build
# http://localhost:8000
```
