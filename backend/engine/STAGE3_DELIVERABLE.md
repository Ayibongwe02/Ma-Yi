# Stage 3 — Context Filters: Deliverable

New file: `engine/context.py` (filters + combiner). Demo/smoke test:
`engine/test_context.py` (run with `python engine/test_context.py`).

## How the final score is built

For every non-zero pattern score from Stage 2, three multipliers are computed
and applied. Final score is clamped to [-100, 100].

```
final_score = raw_pattern_score × trend_mult × sr_mult × volatility_mult
```

**1. Trend filter** (`compute_same_tf_trend` / `compute_htf_trend`)
Price vs. a moving average (default: 50-period EMA) *and* the MA's slope
over the last 5 bars — both have to agree, so a brief poke above a flat
MA doesn't count as "uptrend." Optionally pass `htf_rule="4h"` to
`apply_context_filters()` to use a genuinely resampled higher timeframe
instead (closer to how a discretionary trader reads context).

- Pattern direction agrees with trend → **×1.15**
- Pattern direction fights the trend → **×0.55**
- No clear trend either way → **×0.9**

**2. Support/resistance filter** (`sr_confluence`)
Finds the nearest recent swing high/low (fractal: a bar whose high/low is
the extreme of its ±3-bar neighborhood, within the last 50 bars) and the
nearest round-number level (default grid: 0.0050 — tune per instrument,
e.g. USDJPY needs a bigger increment). Produces a 0–1 proximity score.

- Bullish pattern forming right at support (or a round number), or bearish
  at resistance → multiplier **1.0 to 1.35** scaled by closeness
- A level is nearby but on the wrong side (e.g. bullish pattern right under
  overhead resistance) → **discounted**, scaled by closeness (down to 0.75)
- No level nearby at all → **×0.85** (flat penalty — no location edge)

**3. Volatility filter** (ATR-based, `volatility_state`)
Candle range vs. the *prior* 14-period ATR (no lookahead):

- Range ≥ 2.5× ATR → `"spike"` (likely a news event) → **×0.15**, effectively
  killing the score rather than trusting a pattern formed in a volatility
  blowout
- Range ≤ 0.4× ATR → `"quiet"` → **×0.85**, mild discount for a dead market
- Otherwise → `"normal"` → **×1.05**, small bonus for clean conditions

## Before / after examples

Run against 300 synthetic hourly EURUSD candles (`test_context.py` falls
back to synthetic data in this sandbox since it has no network access to
Yahoo Finance — swap in real cached history from Stage 1 on your machine
and the same logic applies unchanged).

| Pattern | Raw | Final | Trend | S/R | Volatility | What happened |
|---|---|---|---|---|---|---|
| `hammer` | 63.1 | **100.0** | +1 (up) | 0.98 | normal | Textbook confluence: bullish pattern, uptrend, right at a level → capped at the max. |
| `engulfing` | 35.7 | **57.8** | +1 (up) | 0.97 | normal | Below the 40-point alert threshold *raw*, but trend + S/R confluence pushed it into signal territory. |
| `engulfing` | 100.0 | **18.1** | 0 (none) | 0.98 | **spike** | Textbook engulfing, but the candle range was 2.5x+ ATR — flagged as a likely news spike and killed. |
| `three_soldiers_crows` | 99.3 | **16.1** | +1 | 0.23 | **spike** | Same story: strong raw pattern, but forming during an abnormal-volatility bar, so it's discounted hard. |
| `engulfing` | 48.3 | **23.7** | **-1 (conflict)** | 0.00 | normal | Bullish pattern fighting a downtrend, with no S/R level nearby to justify a reversal — dropped below the threshold. |
| `shooting_star` | -47.4 | **-36.2** | +1 (conflict for bearish) | 0.93 | normal | Bearish pattern forming *inside* an uptrend — trend penalty outweighs a nearby level, so it slips under threshold. |

Full run on the 300-candle sample: **207 signals confirmed** (raw and final
both cleared the 40-point threshold), **30 killed** (raw cleared 40, context
dragged it under), **13 context-boosted** (raw was under 40, context pushed
it over). That last group is exactly the "no context support = low score,
full confluence = high score" behavior the stage asked for — the filters
aren't just a haircut on strong patterns, they can also surface a
medium-quality pattern that has real context behind it.

## Tuning knobs (all keyword args on `apply_context_filters`)

`ma_period`, `ma_type` ("ema"/"sma"), `htf_rule` (e.g. "4h" for true HTF
trend), `swing_window`, `sr_lookback`, `sr_tol_frac`, `round_increment`
(instrument-specific — 0.0050 for EURUSD-like pairs, use ~0.5 for JPY
pairs), `atr_period`, `spike_mult`, `quiet_mult`. The multiplier constants
themselves (1.15/0.55/0.9 for trend, 1.35/0.75/0.85 for S/R, 0.15/0.85/1.05
for volatility) are inline in `apply_context_filters` — pull them out to
named constants if you want to sweep them during Stage 4 tuning.
