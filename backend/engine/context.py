"""
Stage 3 — Context filters.

Takes the raw pattern scores from Stage 2 (engine/patterns.py) and adjusts
each one based on whether the surrounding market context supports it:

1. Trend filter      — does the pattern's direction agree with the prevailing
                        trend (price vs. a moving average, with slope), or
                        optionally a true higher-timeframe trend computed by
                        resampling the feed and checking HTF candle/MA
                        direction?
2. Support/resistance — is the pattern forming near a recent swing high/low
                        or a round-number ("psychological") price level?
3. Market structure   — has price recently broken a confirmed swing extreme
                        (BOS = continuation, CHoCH = the first sign of a
                        reversal)? This is more responsive than the MA
                        trend, which only reacts after enough bars accrue.
4. Supply/demand zones — is the pattern forming inside the base candle that
                        preceded a strong impulsive move away from it? The
                        classic "smart money" read: the last candle before
                        price left in a hurry is where it's expected to
                        react again if revisited (more so if never retested).
5. Volatility filter  — is the candle's range consistent with recent ATR, or
                        is it a spike (likely a news event) that should be
                        discounted/excluded, or abnormally quiet (low
                        conviction either way)?

Each filter produces a multiplier; multipliers combine with the raw pattern
score to produce a single final signal score. A pattern with no context
support decays toward 0. A pattern with several filters in confluence gets
boosted (capped at +/-100).

This module does NOT re-detect patterns — it consumes the output of
detect_patterns() from patterns.py and returns a same-shaped DataFrame of
adjusted scores, plus diagnostic columns so you can see *why* a score moved.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# ---------- trend filter ----------

def compute_trend_ma(df: pd.DataFrame, period: int = 50, ma_type: str = "ema") -> pd.Series:
    if ma_type == "ema":
        return df.Close.ewm(span=period, adjust=False).mean()
    return df.Close.rolling(period, min_periods=1).mean()


def _trend_state(df: pd.DataFrame, ma: pd.Series, i: int, slope_lookback: int = 5) -> int:
    """+1 uptrend, -1 downtrend, 0 no clear trend. Requires price on the
    correct side of the MA *and* the MA sloping the same way, so a price
    poking above a flat/falling MA doesn't count as an uptrend."""
    if i < slope_lookback:
        return 0
    price = df.Close.iloc[i]
    ma_now, ma_prev = ma.iloc[i], ma.iloc[i - slope_lookback]
    slope = ma_now - ma_prev
    if price > ma_now and slope > 0:
        return 1
    if price < ma_now and slope < 0:
        return -1
    return 0


def compute_same_tf_trend(df: pd.DataFrame, period: int = 50, ma_type: str = "ema") -> pd.Series:
    """Trend state computed directly on the signal timeframe via MA + slope."""
    ma = compute_trend_ma(df, period, ma_type)
    states = [_trend_state(df, ma, i) for i in range(len(df))]
    return pd.Series(states, index=df.index)


def compute_htf_trend(df: pd.DataFrame, htf_rule: str = "4h", ma_period: int = 20) -> pd.Series:
    """
    True higher-timeframe trend: resample to htf_rule (e.g. '4h', '1d'),
    compute MA + slope trend on those candles, then forward-fill each HTF
    trend state onto the original (lower) timeframe index. Bars before the
    first HTF candle closes get 0 (no trend read yet).
    """
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    htf = df.resample(htf_rule).agg(agg).dropna(how="any")
    htf_ma = compute_trend_ma(htf, ma_period)
    htf_states = pd.Series(
        [_trend_state(htf, htf_ma, i) for i in range(len(htf))], index=htf.index
    )
    aligned = htf_states.reindex(df.index, method="ffill").fillna(0)
    return aligned


# ---------- short-term trend: strength + persistence ----------
#
# These support the "long trend is the strategy, short-term trend is just
# noise unless proven otherwise" policy in apply_context_filters(): a short
# move is only worth trading against the long trend if it's both young
# (hasn't been running long / "not yet noticed") and has real momentum
# behind it (not just chop).

def _trend_strength(df: pd.DataFrame, ma: pd.Series, i: int, slope_lookback: int = 5) -> float:
    """Normalized MA slope magnitude (slope / MA level) at bar i. 0 when
    there isn't enough history yet or the MA level is 0."""
    if i < slope_lookback:
        return 0.0
    ma_now, ma_prev = ma.iloc[i], ma.iloc[i - slope_lookback]
    if not ma_now:
        return 0.0
    return abs(ma_now - ma_prev) / abs(ma_now)


def compute_trend_strength(df: pd.DataFrame, ma: pd.Series, slope_lookback: int = 5) -> pd.Series:
    return pd.Series(
        [_trend_strength(df, ma, i, slope_lookback) for i in range(len(df))], index=df.index
    )


def compute_state_persistence(states: pd.Series) -> pd.Series:
    """How many consecutive bars (including the current one) the trend
    state has held its current non-zero value. Resets to 1 whenever the
    state changes, 0 while state is 0 (no clear trend). A low value means
    the trend just flipped -- i.e. it's fresh, "before it's been noticed"."""
    persist = np.zeros(len(states), dtype=int)
    run = 0
    prev = None
    for idx, s in enumerate(states):
        if s != 0 and s == prev:
            run += 1
        elif s != 0:
            run = 1
        else:
            run = 0
        persist[idx] = run
        prev = s
    return pd.Series(persist, index=states.index)


# ---------- support / resistance filter ----------

def _is_swing_high(df: pd.DataFrame, i: int, window: int = 3) -> bool:
    if i < window or i > len(df) - 1 - window:
        return False
    seg = df.High.iloc[i - window: i + window + 1]
    return df.High.iloc[i] == seg.max()


def _is_swing_low(df: pd.DataFrame, i: int, window: int = 3) -> bool:
    if i < window or i > len(df) - 1 - window:
        return False
    seg = df.Low.iloc[i - window: i + window + 1]
    return df.Low.iloc[i] == seg.min()


def compute_swing_levels(df: pd.DataFrame, window: int = 3):
    """Fractal-style swing points: a high/low that is the extreme of its
    +/-window neighborhood. Returns (highs, lows) as lists of (bar_index, price)."""
    highs, lows = [], []
    for i in range(len(df)):
        if _is_swing_high(df, i, window):
            highs.append((i, df.High.iloc[i]))
        if _is_swing_low(df, i, window):
            lows.append((i, df.Low.iloc[i]))
    return highs, lows


def _round_number_proximity(price: float, increment: float = 0.0050, tol_frac: float = 0.15) -> float:
    """0..1 closeness score to the nearest round-number level (big figures /
    half-figures for FX, e.g. 1.0800 / 1.0850). `increment` is instrument-
    dependent — 0.0050 suits ~1.xxxx majors; tune per pair (e.g. USDJPY
    would use ~0.50, not 0.0050)."""
    nearest = round(price / increment) * increment
    dist = abs(price - nearest)
    tol = increment * tol_frac
    if dist <= tol:
        return round(1 - dist / tol, 3)
    return 0.0


def sr_confluence(
    df: pd.DataFrame, swing_highs, swing_lows, i: int, pattern_direction: int,
    lookback: int = 50, tol_frac: float = 0.0012, round_increment: float = 0.0050,
):
    """
    Returns (level_score in [0,1], agrees: bool).
    level_score: how close price is to *some* known level (swing or round
    number). agrees: whether that level sits on the side the pattern needs
    — bullish patterns want support below, bearish patterns want resistance
    above. A pattern near the *wrong*-side level (e.g. bullish reversal
    right under overhead resistance) is flagged as not agreeing even though
    a level is nearby, since that's a headwind, not confluence.
    """
    price = df.Close.iloc[i]
    recent_lows = [lvl for idx, lvl in swing_lows if idx < i and i - idx <= lookback]
    recent_highs = [lvl for idx, lvl in swing_highs if idx < i and i - idx <= lookback]
    nearest_low = min(recent_lows, key=lambda lvl: abs(lvl - price), default=None)
    nearest_high = min(recent_highs, key=lambda lvl: abs(lvl - price), default=None)

    tol = price * tol_frac
    at_support = nearest_low is not None and abs(price - nearest_low) <= tol
    at_resistance = nearest_high is not None and abs(price - nearest_high) <= tol

    level_score = 0.0
    if at_support:
        level_score = max(level_score, 1 - abs(price - nearest_low) / tol)
    if at_resistance:
        level_score = max(level_score, 1 - abs(price - nearest_high) / tol)
    round_score = _round_number_proximity(price, round_increment)
    level_score = max(level_score, round_score)

    if pattern_direction > 0:
        agrees = at_support or (round_score > 0 and not at_resistance)
    elif pattern_direction < 0:
        agrees = at_resistance or (round_score > 0 and not at_support)
    else:
        agrees = level_score > 0

    return round(level_score, 3), agrees


# ---------- market structure (Break of Structure / Change of Character) ----------
#
# More responsive than the MA trend, which only flips after enough bars
# accrue: this reacts the moment price actually breaks a confirmed swing
# extreme, using *only* swing points confirmed before the current bar (no
# lookahead — same discipline as sr_confluence's recent_lows/recent_highs).

def compute_market_structure(df: pd.DataFrame, swing_highs, swing_lows) -> pd.Series:
    """
    At each bar, read the two most recently *confirmed* swing highs and two
    most recently confirmed swing lows (idx < current bar) to classify the
    standing structure as bullish (higher highs & higher lows), bearish
    (lower highs & lower lows), or ranging/undefined otherwise. Then check
    whether the current close breaks the most recent confirmed extreme:

      BOS   (break of structure)   — close breaks the extreme in the
            direction structure already pointed: a continuation signal.
      CHoCH (change of character)  — close breaks the extreme *against* an
            established opposite-direction structure: the first sign a
            reversal may be starting.

    Returns a Series of event strings per bar (one of 'bullish_bos',
    'bearish_bos', 'bullish_choch', 'bearish_choch', or '' for no event).
    """
    n = len(df)
    events = [""] * n
    sh = sorted(swing_highs, key=lambda t: t[0])
    sl = sorted(swing_lows, key=lambda t: t[0])
    hi_idx = lo_idx = 0
    confirmed_highs: list[float] = []
    confirmed_lows: list[float] = []
    for i in range(n):
        while hi_idx < len(sh) and sh[hi_idx][0] < i:
            confirmed_highs.append(sh[hi_idx][1])
            hi_idx += 1
        while lo_idx < len(sl) and sl[lo_idx][0] < i:
            confirmed_lows.append(sl[lo_idx][1])
            lo_idx += 1
        if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
            continue
        h1, h2 = confirmed_highs[-2], confirmed_highs[-1]
        l1, l2 = confirmed_lows[-2], confirmed_lows[-1]
        if h2 > h1 and l2 > l1:
            structure = "bullish"
        elif h2 < h1 and l2 < l1:
            structure = "bearish"
        else:
            structure = "ranging"

        close = df.Close.iloc[i]
        if close > h2:
            events[i] = "bullish_choch" if structure == "bearish" else "bullish_bos"
        elif close < l2:
            events[i] = "bearish_choch" if structure == "bullish" else "bearish_bos"
    return pd.Series(events, index=df.index)


def _structure_multiplier(recent_event: str, direction: int) -> float:
    """How much a recent structure event should adjust a pattern's score,
    given the pattern's own direction. Confluence (event agrees with the
    pattern) boosts, with BOS boosting more than CHoCH since BOS confirms
    an already-established move while CHoCH is only the *first* sign of one.
    A CHoCH against the pattern is the strongest headwind — the market just
    showed its character changing away from what the pattern needs."""
    if not recent_event:
        return 1.0
    bullish_event = recent_event.startswith("bullish")
    is_bos = recent_event.endswith("bos")
    agrees = (bullish_event and direction > 0) or (not bullish_event and direction < 0)
    if agrees:
        return 1.20 if is_bos else 1.10
    return 0.75 if is_bos else 0.55


# ---------- supply / demand zones ----------
#
# A zone is the base candle immediately preceding a strong impulsive move —
# the last candle before price left in a hurry. Price returning to that
# candle's range is expected to react again, more so if it's never been
# retested (a "fresh" zone) than if it already has (partially used up).

def compute_zones(
    df: pd.DataFrame, atr: pd.Series, impulse_atr_mult: float = 1.6, max_zone_age: int = 150,
) -> tuple[list[dict], list[dict]]:
    """
    Scan for single-candle impulsive moves (body >= impulse_atr_mult times
    the *prior* bar's ATR, so zone formation never looks ahead) and mark the
    candle immediately before each one as a zone. Bullish impulses mark a
    demand zone at the base candle below; bearish impulses mark a supply
    zone at the base candle above.

    Each zone also gets `first_test`: the index of the first later bar whose
    range overlaps the zone (or None if never retested in this dataset).
    That index is computed with full knowledge of the future, but callers
    must only compare it against the *current scoring bar* (`< i`) so a
    zone is never treated as "already tested" before that test actually
    happened — see zone_confluence().

    Returns (demand_zones, supply_zones), each a list of dicts with keys
    top, bottom, formed_at, impulse_at, first_test.
    """
    n = len(df)
    demand: list[dict] = []
    supply: list[dict] = []
    for i in range(1, n):
        prior_atr = atr.iloc[i - 1]
        if prior_atr <= 0:
            continue
        body = df.Close.iloc[i] - df.Open.iloc[i]
        base = df.iloc[i - 1]
        zone = {
            "top": float(base.High), "bottom": float(base.Low),
            "formed_at": i - 1, "impulse_at": i, "first_test": None,
        }
        if body >= impulse_atr_mult * prior_atr:
            demand.append(zone)
        elif -body >= impulse_atr_mult * prior_atr:
            supply.append(zone)

    def _first_test(zone: dict) -> Optional[int]:
        top, bottom = zone["top"], zone["bottom"]
        for j in range(zone["impulse_at"] + 1, n):
            if df.Low.iloc[j] <= top and df.High.iloc[j] >= bottom:
                return j
        return None

    for z in demand + supply:
        z["first_test"] = _first_test(z)
    return demand, supply


def zone_confluence(
    demand_zones: list[dict], supply_zones: list[dict], i: int, direction: int, price: float,
    max_zone_age: int = 150,
) -> tuple[float, bool, bool]:
    """
    Returns (zone_score in [0,1], agrees, fresh).
    A bullish pattern (direction > 0) checks demand zones for confluence and
    supply zones as a headwind (price sitting under overhead supply);
    bearish is the mirror. `agrees` mirrors sr_confluence's semantics: True
    only when price is inside a same-side zone. `fresh` means the zone
    hasn't been retested yet as of bar i (its first_test, if any, is not
    strictly before i) — the highest-quality read on a zone.
    """
    same = demand_zones if direction > 0 else supply_zones
    opposite = supply_zones if direction > 0 else demand_zones

    def _active_inside(z: dict) -> bool:
        return (
            z["formed_at"] < i
            and (i - z["formed_at"]) <= max_zone_age
            and z["bottom"] <= price <= z["top"]
        )

    same_hits = [z for z in same if _active_inside(z)]
    if same_hits:
        z = same_hits[0]
        fresh = z["first_test"] is None or z["first_test"] >= i
        return (1.0 if fresh else 0.6), True, fresh

    opp_hits = [z for z in opposite if _active_inside(z)]
    if opp_hits:
        return 0.5, False, False

    return 0.0, False, False


def _zone_multiplier(zone_score: float, agrees: bool, fresh: bool) -> float:
    if agrees:
        return 1.25 if fresh else 1.10
    if zone_score > 0:  # sitting inside the opposing zone — a headwind
        return 0.65
    return 1.0


def _zone_label(zone_score: float, agrees: bool, fresh: bool, direction: int) -> str:
    if agrees:
        side = "demand" if direction > 0 else "supply"
        return f"{side}_fresh" if fresh else f"{side}_tested"
    if zone_score > 0:
        return "opposing_zone"
    return "none"


# ---------- volatility filter (ATR) ----------

def _true_range(df: pd.DataFrame, i: int) -> float:
    row = df.iloc[i]
    if i == 0:
        return row.High - row.Low
    prev_close = df.Close.iloc[i - 1]
    return max(row.High - row.Low, abs(row.High - prev_close), abs(row.Low - prev_close))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.Series([_true_range(df, i) for i in range(len(df))], index=df.index)
    return tr.rolling(period, min_periods=1).mean()


def volatility_state(
    df: pd.DataFrame, atr: pd.Series, i: int, spike_mult: float = 2.5, quiet_mult: float = 0.4,
) -> str:
    """Classify the current candle's range vs. *prior* ATR (no lookahead):
    'spike' (likely news), 'quiet' (dead market), or 'normal'."""
    if i == 0:
        return "normal"
    candle_range = df.High.iloc[i] - df.Low.iloc[i]
    prior_atr = atr.iloc[i - 1]
    if prior_atr <= 0:
        return "normal"
    ratio = candle_range / prior_atr
    if ratio >= spike_mult:
        return "spike"
    if ratio <= quiet_mult:
        return "quiet"
    return "normal"


# ---------- combine into final score ----------

def apply_context_filters(
    df: pd.DataFrame,
    pattern_df: pd.DataFrame,
    ma_period: int = 50,
    ma_type: str = "ema",
    htf_rule: str | None = "4h",
    htf_ma_period: int = 20,
    swing_window: int = 3,
    sr_lookback: int = 50,
    sr_tol_frac: float = 0.0012,
    round_increment: float = 0.0050,
    atr_period: int = 14,
    spike_mult: float = 2.5,
    quiet_mult: float = 0.4,
    short_ma_period: int = 20,
    short_slope_lookback: int = 5,
    counter_trend_policy: str = "ignore_short",
    early_bars: int = 3,
    min_counter_strength: float = 0.0015,
    structure_lookback: int = 5,
    zone_impulse_atr_mult: float = 1.6,
    zone_max_age: int = 150,
) -> pd.DataFrame:
    """
    df: OHLC candles. pattern_df: output of patterns.detect_patterns(df).

    Trend is now split into two reads:

      long trend  — the strategic bias. A true resampled higher timeframe
                    trend (htf_rule, e.g. '4h'/'1d') when htf_rule is given,
                    else an MA+slope trend on the signal timeframe using
                    ma_period. This is what the system trades *with* by
                    default.
      short trend — an MA+slope trend on the signal timeframe using the
                    (shorter) short_ma_period. Treated as short-term noise
                    unless it's both young (persistence <= early_bars, i.e.
                    it flipped recently, "before it's been noticed") and
                    has real momentum (strength >= min_counter_strength).

    counter_trend_policy controls what happens when a pattern's direction
    disagrees with the long trend:
      "ignore_short" (default) — always suppressed. Only long-trend-aligned
                    signals get through at strength; short-term counter
                    moves are treated purely as noise, per a long-trend-only
                    strategy.
      "fade_early"  — a pattern that disagrees with the long trend but is
                    backed by a young + strong short trend in its own
                    direction is allowed through as a discounted counter-
                    trend ("fade") trade, on the theory that catching it
                    early is worth more than waiting for the long trend to
                    reassert. A short trend that's already mature (running
                    longer than early_bars) no longer counts as "early" —
                    it's either already priced in or is possibly becoming
                    the new long trend, neither of which this policy chases.

    structure_lookback bars are searched backward for the most recent
    market-structure event (BOS/CHoCH — see compute_market_structure) to
    factor into the score; zone_impulse_atr_mult / zone_max_age control how
    supply/demand zones are detected and how long they stay active (see
    compute_zones).

    Returns a DataFrame indexed like pattern_df with, per pattern column:
      <pattern>            final context-adjusted score (signed, +/-100)
      <pattern>_trend      long-trend state at that bar (+1/-1/0)
      <pattern>_short_trend  short-trend state at that bar (+1/-1/0)
      <pattern>_regime      'with_long_trend' | 'counter_trend_early' |
                             'counter_trend_ignored' | 'no_long_trend'
      <pattern>_sr          S/R proximity score used (0..1)
      <pattern>_structure   most recent BOS/CHoCH event within
                             structure_lookback bars ('bullish_bos',
                             'bearish_choch', ... or 'none')
      <pattern>_zone        supply/demand zone read ('demand_fresh',
                             'demand_tested', 'supply_fresh',
                             'supply_tested', 'opposing_zone', 'none')
      <pattern>_vol         volatility state ('normal'/'spike'/'quiet')
    """
    if counter_trend_policy not in ("ignore_short", "fade_early"):
        raise ValueError("counter_trend_policy must be 'ignore_short' or 'fade_early'")

    n = len(df)
    long_trend_states = (
        compute_htf_trend(df, htf_rule, htf_ma_period) if htf_rule
        else compute_same_tf_trend(df, ma_period, ma_type)
    )
    short_ma = compute_trend_ma(df, short_ma_period, ma_type)
    short_trend_states = compute_same_tf_trend(df, short_ma_period, ma_type)
    short_strength = compute_trend_strength(df, short_ma, short_slope_lookback)
    short_persist = compute_state_persistence(short_trend_states)

    atr = compute_atr(df, atr_period)
    swing_highs, swing_lows = compute_swing_levels(df, swing_window)
    structure_events = compute_market_structure(df, swing_highs, swing_lows)
    demand_zones, supply_zones = compute_zones(df, atr, zone_impulse_atr_mult, zone_max_age)

    pattern_cols = list(pattern_df.columns)
    out = {c: np.zeros(n) for c in pattern_cols}
    out.update({f"{c}_trend": np.zeros(n) for c in pattern_cols})
    out.update({f"{c}_short_trend": np.zeros(n) for c in pattern_cols})
    out.update({f"{c}_regime": np.array([""] * n, dtype=object) for c in pattern_cols})
    out.update({f"{c}_sr": np.zeros(n) for c in pattern_cols})
    out.update({f"{c}_structure": np.array([""] * n, dtype=object) for c in pattern_cols})
    out.update({f"{c}_zone": np.array([""] * n, dtype=object) for c in pattern_cols})
    out.update({f"{c}_vol": np.array(["normal"] * n, dtype=object) for c in pattern_cols})

    vol_states = [volatility_state(df, atr, i, spike_mult, quiet_mult) for i in range(n)]

    for i in range(n):
        vol_state = vol_states[i]
        vol_mult = {"spike": 0.15, "quiet": 0.85, "normal": 1.05}[vol_state]

        long_state = long_trend_states.iloc[i]
        short_state = short_trend_states.iloc[i]
        persist = short_persist.iloc[i]
        strength = short_strength.iloc[i]
        price = df.Close.iloc[i]

        window = structure_events.iloc[max(0, i - structure_lookback + 1): i + 1]
        recent_structure = next((e for e in reversed(window.tolist()) if e), "")

        for col in pattern_cols:
            raw = pattern_df[col].iloc[i]
            if raw == 0:
                continue
            direction = 1 if raw > 0 else -1

            long_agree = long_state == direction
            long_conflict = long_state == -direction

            if long_agree:
                trend_mult = 1.15
                regime = "with_long_trend"
            elif long_conflict:
                short_supports = short_state == direction
                is_early = 0 < persist <= early_bars
                is_strong = strength >= min_counter_strength
                if counter_trend_policy == "fade_early" and short_supports and is_early and is_strong:
                    trend_mult = 0.9
                    regime = "counter_trend_early"
                else:
                    trend_mult = 0.35
                    regime = "counter_trend_ignored"
            else:
                trend_mult = 0.9
                regime = "no_long_trend"

            sr_score, sr_agree = sr_confluence(
                df, swing_highs, swing_lows, i, direction,
                sr_lookback, sr_tol_frac, round_increment,
            )
            sr_mult = (1.0 + 0.35 * sr_score) if sr_agree else (1.0 - 0.25 * sr_score if sr_score > 0 else 0.85)

            structure_mult = _structure_multiplier(recent_structure, direction)

            zone_score, zone_agree, zone_fresh = zone_confluence(
                demand_zones, supply_zones, i, direction, price, zone_max_age,
            )
            zone_mult = _zone_multiplier(zone_score, zone_agree, zone_fresh)

            final = raw * trend_mult * sr_mult * structure_mult * zone_mult * vol_mult
            final = max(-100.0, min(100.0, final))

            out[col][i] = round(final, 1)
            out[f"{col}_trend"][i] = long_state
            out[f"{col}_short_trend"][i] = short_state
            out[f"{col}_regime"][i] = regime
            out[f"{col}_sr"][i] = sr_score
            out[f"{col}_structure"][i] = recent_structure or "none"
            out[f"{col}_zone"][i] = _zone_label(zone_score, zone_agree, zone_fresh, direction)
            out[f"{col}_vol"][i] = vol_state

    return pd.DataFrame(out, index=df.index)


def compare_before_after(
    pattern_df: pd.DataFrame, context_df: pd.DataFrame, pattern_cols: list[str], min_score: float = 40,
) -> pd.DataFrame:
    """Tidy before/after table: every bar where the raw or the final score
    crossed the threshold, so you can see signals context confirmed, ones it
    killed, and ones it created (rare — multipliers only push existing
    non-zero scores)."""
    rows = []
    for col in pattern_cols:
        raw, final = pattern_df[col], context_df[col]
        mask = (raw.abs() >= min_score) | (final.abs() >= min_score)
        for ts in raw[mask].index:
            rows.append({
                "timestamp": ts,
                "pattern": col,
                "raw_score": raw.loc[ts],
                "final_score": final.loc[ts],
                "delta": round(final.loc[ts] - raw.loc[ts], 1),
                "trend": int(context_df[f"{col}_trend"].loc[ts]),
                "short_trend": int(context_df[f"{col}_short_trend"].loc[ts]),
                "regime": context_df[f"{col}_regime"].loc[ts],
                "sr_score": context_df[f"{col}_sr"].loc[ts],
                "structure": context_df[f"{col}_structure"].loc[ts],
                "zone": context_df[f"{col}_zone"].loc[ts],
                "volatility": context_df[f"{col}_vol"].loc[ts],
                "verdict": (
                    "confirmed" if abs(final.loc[ts]) >= min_score and abs(raw.loc[ts]) >= min_score
                    else "killed" if abs(raw.loc[ts]) >= min_score
                    else "context-boosted"
                ),
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values("timestamp").reset_index(drop=True)
