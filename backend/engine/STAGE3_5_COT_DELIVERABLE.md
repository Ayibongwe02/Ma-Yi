# Stage 3.5 — Institutional Positioning (COT): Deliverable

New file: `data/cot.py` (CFTC fetch + "COT Index" scoring). Demo/smoke
test: `data/test_cot.py` (run with `python data/test_cot.py`).
`engine/confidence.py` extended with `blend_with_cot`,
`retail_institutional_divergence`, and `combine_signals`; new tests in
`engine/test_confidence.py`. `delivery/runner.py` wires it into live mode
alongside existing IG sentiment.

## Why this is the natural complement to Stage 3's sentiment

`data/sentiment.py` gives a **contrarian retail** read: IG's Client
Sentiment API tells you what the retail crowd is doing, and the score
fades it (heavily long crowd → bearish score). That's a real edge, but
retail sentiment alone doesn't tell you what *institutions* are doing —
and retail-vs-institutional divergence (retail heavily long while large
speculators are heavily short, or vice versa) is a stronger signal than
retail positioning by itself.

`data/cot.py` adds that missing half: the CFTC's weekly **Commitment of
Traders** report, specifically the "Legacy Futures Only" report's
non-commercial (large speculator) positions — free, no signup, no API key,
via CFTC's public Socrata endpoint.

## Data source and scoring

- Endpoint: `https://publicreporting.cftc.gov/resource/6dca-aqww.json`
  ("Commitments of Traders (Legacy Report) - Futures Only"). Queried per
  currency futures contract (`CFTC_CODES` in `data/cot.py`: EUR, GBP, JPY,
  AUD, CAD, CHF, NZD, USD-index), newest-first, trailing 156 weekly reports
  (~3 years) by default.
- Net position = non-commercial long − non-commercial short, expressed as
  % of open interest (`net_pct_oi`) so the metric stays comparable as open
  interest drifts over the years.
- **COT Index** (classic Larry Williams / Stephen Briese formulation):
  where the current `net_pct_oi` sits within its own trailing range,
  0-100. 100 = large specs at their most net-long for this currency within
  the lookback; 0 = most net-short; 50 = dead center.
- Rescaled to the engine's -100..+100 scale via `(index - 50) * 2`.
- **Not contrarian** — unlike sentiment, a positive COT score means large
  speculators themselves are positioned bullish. That directionality is
  exactly what makes it useful *against* the contrarian retail score.
- Weekly cache (`data/store/cot_<CODE>.json`) refetched at most every ~3
  days; a stale cache is used as a fallback if a refetch fails, so a
  transient CFTC outage doesn't kill the overlay.

## Currency → pair mapping (`Instrument.cot_legs` in `data/instruments.py`)

COT is published per currency futures contract, not per pair, so each
instrument declares which leg(s) drive its score and the sign of each:

| Pair | Legs | Why |
|---|---|---|
| EURUSD=X | `EUR +1` | net-long EUR futures → bullish EUR → bullish EURUSD |
| GBPUSD=X | `GBP +1` | same logic for GBP |
| USDJPY=X | `JPY -1` | USD is the *base* here — net-long JPY (bullish JPY) → bearish USDJPY |
| GBPJPY=X | `GBP +1`, `JPY -1` | cross — average both legs |
| ^DJI | none | no FX-currency COT mapping for an equity index (mirrors `ig_market_id=None`) |

## Combining sentiment + COT (`engine/confidence.py`)

```
score = blend_with_sentiment(pattern_score, sentiment_score)   # Stage 3, unchanged
score = blend_with_cot(score, cot_score)                       # Stage 3.5
score *= divergence_multiplier(sentiment_score, cot_score)     # confluence check
```

`retail_institutional_divergence(sentiment_score, cot_score)`:
- Both scores must clear `COT_DIVERGENCE_BONUS`'s companion magnitude
  threshold (default 30 on the -100..100 scale) — two near-neutral
  readings "agreeing" isn't a meaningful confluence.
- **Same sign** (e.g. sentiment +60, COT +60 → retail crowd heavily
  *short*, large specs net *long*) → institutions agree with the
  contrarian fade → `combine_signals` multiplies the blended score by
  `(1 + COT_DIVERGENCE_BONUS)` (default ×1.20).
- **Opposite sign** (retail crowded one way, institutions leaning the
  *other* way from what the contrarian read implies — i.e. institutions
  are leaning with the retail crowd) → multiply by
  `(1 - COT_DIVERGENCE_BONUS)` (default ×0.80), since that undercuts the
  contrarian thesis.
- Either score missing/zero/weak → multiplier is 1 (no-op).

`combine_signals()` is a strict superset of the old `blend_with_sentiment`
call in `delivery/runner.py`: with `cot_score=None` it reduces to exactly
the Stage-3 behavior, and with both overlays `None` it's a pass-through —
nothing about the existing pattern/context/sentiment pipeline changes when
COT is unavailable (unmapped instrument, CFTC outage, etc).

## Same live-only asymmetry as sentiment

Both overlays only ever touch the **final (live) bar** in
`delivery/runner.py::live_once()` → `process_dataframe(..., sentiment_score=,
cot_score=)`. Replay/backtest runs never see either — CFTC's weekly report
does technically have history (see `cot_index(records, as_of_index=...)`
for point-in-time scoring without lookahead, already exposed for anyone
who wants to wire COT into `backtest/engine.py` later), but this stage
keeps it symmetric with sentiment rather than mixing "has history" and
"live-only" overlays in the same live/backtest split.

## Config (`config.example.env`)

```
COT_WEIGHT=0.15            # blend weight, lighter default than sentiment's
                            # 0.2 since COT is always a few days stale
COT_DIVERGENCE_BONUS=0.2   # +/-20% conviction on confirmed/contradicted
                            # retail-vs-institutional divergence
```

## Tuning / extension notes

- `DEFAULT_LOOKBACK_WEEKS=156` in `data/cot.py` (COT Index window) and
  `DEFAULT_DIVERGENCE_MIN_MAGNITUDE=30.0` in `engine/confidence.py` are
  the main knobs to sweep during Stage 4-style tuning.
- Adding a currency: add its CFTC contract code to `CFTC_CODES`, then set
  `cot_legs` on the relevant `Instrument`(s) in `data/instruments.py`.
- This sandbox has no network access to `publicreporting.cftc.gov` (same
  restriction Stage 3 hit with IG) — `data/test_cot.py` falls back to a
  mocked report sequence to validate the COT Index math, exactly like
  `data/test_sentiment.py` does for IG. Real data flows once that host is
  reachable (see `data/cot.py` module docstring).
