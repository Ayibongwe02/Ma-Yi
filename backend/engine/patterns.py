"""
Stage 2 — Candlestick pattern recognition.

Given a DataFrame of OHLC candles (same shape produced by data/candles.py),
detect classic candlestick patterns on each candle / candle sequence and
return a confidence score (0-100), not just a boolean. Score reflects how
"textbook" the pattern is (body/wick ratios, relative size vs recent
candles) — it does NOT yet know about trend, support/resistance, or
volatility context. That's Stage 3.

Output: a DataFrame aligned to the input index, with one column per
pattern holding a score (0 = not present, higher = stronger match) and a
`direction` sign baked into the sign of the score (+ve = bullish,
-ve = bearish) for two-directional patterns like engulfing/doji-adjacent
reversals. Single-direction patterns (hammer, shooting star, etc.) are
always reported with their natural direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------- helpers ----------

def _body(row) -> float:
    return abs(row.Close - row.Open)


def _range(row) -> float:
    return max(row.High - row.Low, 1e-12)  # avoid div-by-zero on flat candles


def _upper_wick(row) -> float:
    return row.High - max(row.Open, row.Close)


def _lower_wick(row) -> float:
    return min(row.Open, row.Close) - row.Low


def _is_bullish(row) -> bool:
    return row.Close > row.Open


def _avg_body(df: pd.DataFrame, i: int, lookback: int = 14) -> float:
    start = max(0, i - lookback)
    window = df.iloc[start:i]
    if len(window) == 0:
        return _body(df.iloc[i])
    return (window.Close - window.Open).abs().mean() or 1e-12


# ---------- single-candle patterns ----------

def _score_hammer(row, avg_body: float) -> float:
    """Bullish reversal: small body near top, long lower wick, little/no upper wick."""
    body, rng = _body(row), _range(row)
    lower, upper = _lower_wick(row), _upper_wick(row)
    if rng == 0 or body / rng > 0.35:
        return 0.0
    if lower < body * 2:
        return 0.0
    if upper > body * 0.6:
        return 0.0
    ratio_score = min(lower / max(body, rng * 0.05), 5) / 5 * 70
    size_score = min(body / avg_body, 1.5) / 1.5 * 30
    return round(ratio_score + size_score, 1)


def _score_shooting_star(row, avg_body: float) -> float:
    """Bearish reversal: small body near bottom, long upper wick."""
    body, rng = _body(row), _range(row)
    lower, upper = _lower_wick(row), _upper_wick(row)
    if rng == 0 or body / rng > 0.35:
        return 0.0
    if upper < body * 2:
        return 0.0
    if lower > body * 0.6:
        return 0.0
    ratio_score = min(upper / max(body, rng * 0.05), 5) / 5 * 70
    size_score = min(body / avg_body, 1.5) / 1.5 * 30
    return round(-(ratio_score + size_score), 1)  # negative = bearish


def _score_doji(row) -> float:
    """Indecision: body is tiny relative to range. Direction-neutral (reported as magnitude only)."""
    body, rng = _body(row), _range(row)
    if rng == 0:
        return 0.0
    body_pct = body / rng
    if body_pct > 0.1:
        return 0.0
    return round((1 - body_pct / 0.1) * 100, 1)


def _score_marubozu(row, avg_body: float) -> float:
    """Strong conviction candle: full body, negligible wicks either side."""
    body, rng = _body(row), _range(row)
    if rng == 0:
        return 0.0
    wick_pct = (_upper_wick(row) + _lower_wick(row)) / rng
    if wick_pct > 0.08:
        return 0.0
    size_score = min(body / avg_body, 2) / 2 * 100
    score = round(size_score * (1 - wick_pct / 0.08), 1)
    return score if _is_bullish(row) else -score


# ---------- two-candle patterns ----------

def _score_engulfing(prev, curr) -> float:
    prev_body, curr_body = _body(prev), _body(curr)
    if curr_body == 0 or prev_body == 0:
        return 0.0
    bullish_engulf = (
        not _is_bullish(prev) and _is_bullish(curr)
        and curr.Open <= prev.Close and curr.Close >= prev.Open
    )
    bearish_engulf = (
        _is_bullish(prev) and not _is_bullish(curr)
        and curr.Open >= prev.Close and curr.Close <= prev.Open
    )
    if not (bullish_engulf or bearish_engulf):
        return 0.0
    size_score = min(curr_body / prev_body, 3) / 3 * 100
    return round(size_score, 1) if bullish_engulf else round(-size_score, 1)


def _score_tweezer(prev, curr, avg_body: float, tol: float = 0.0008) -> float:
    """Tweezer top/bottom: matching highs (top) or lows (bottom) on two candles.

    `tol` is a *relative* tolerance (abs(diff)/price). Pass instrument-specific
    values from data.instruments.scale_params() — the default 0.0008 is tuned
    for ~1.xxxx majors only.
    """
    high_diff = abs(prev.High - curr.High) / max(prev.High, 1e-9)
    low_diff = abs(prev.Low - curr.Low) / max(prev.Low, 1e-9)

    if high_diff < tol and _is_bullish(prev) and not _is_bullish(curr):
        closeness_score = (1 - high_diff / tol) * 100
        return round(-closeness_score, 1)  # tweezer top = bearish
    if low_diff < tol and not _is_bullish(prev) and _is_bullish(curr):
        closeness_score = (1 - low_diff / tol) * 100
        return round(closeness_score, 1)  # tweezer bottom = bullish
    return 0.0


# ---------- three-candle patterns ----------

def _score_star(c1, c2, c3, avg_body: float, bullish: bool) -> float:
    """Morning star (bullish) / evening star (bearish): big candle, small-body
    gap candle, big candle in the opposite direction, closing well into c1's body."""
    b1, b2, b3 = _body(c1), _body(c2), _body(c3)
    if b1 == 0 or b3 == 0:
        return 0.0
    if bullish:
        if _is_bullish(c1) or b1 < avg_body * 0.8:
            return 0.0
        if b2 > b1 * 0.4:
            return 0.0
        if not _is_bullish(c3):
            return 0.0
        penetration = (c3.Close - c1.Close) / b1
        if penetration < 0.3:
            return 0.0
        return round(min(penetration, 1.5) / 1.5 * 100, 1)
    else:
        if not _is_bullish(c1) or b1 < avg_body * 0.8:
            return 0.0
        if b2 > b1 * 0.4:
            return 0.0
        if _is_bullish(c3):
            return 0.0
        penetration = (c1.Close - c3.Close) / b1
        if penetration < 0.3:
            return 0.0
        return round(-min(penetration, 1.5) / 1.5 * 100, 1)


def _score_three_soldiers_crows(c1, c2, c3, avg_body: float) -> float:
    bodies = [_body(c1), _body(c2), _body(c3)]
    if any(b < avg_body * 0.5 for b in bodies):
        return 0.0

    all_bullish = _is_bullish(c1) and _is_bullish(c2) and _is_bullish(c3)
    all_bearish = not _is_bullish(c1) and not _is_bullish(c2) and not _is_bullish(c3)

    if all_bullish:
        rising = c2.Open > c1.Open and c2.Close > c1.Close and c3.Open > c2.Open and c3.Close > c2.Close
        if not rising:
            return 0.0
        size_score = min(sum(bodies) / (avg_body * 3), 2) / 2 * 100
        return round(size_score, 1)

    if all_bearish:
        falling = c2.Open < c1.Open and c2.Close < c1.Close and c3.Open < c2.Open and c3.Close < c2.Close
        if not falling:
            return 0.0
        size_score = min(sum(bodies) / (avg_body * 3), 2) / 2 * 100
        return round(-size_score, 1)

    return 0.0


# ---------- main entry point ----------

PATTERN_COLUMNS = [
    "hammer", "shooting_star", "doji", "marubozu",
    "engulfing", "tweezer",
    "star", "three_soldiers_crows",
]


def detect_patterns(
    df: pd.DataFrame,
    lookback: int = 14,
    tweezer_tol: float = 0.0008,
) -> pd.DataFrame:
    """
    df: OHLC DataFrame (as produced by data/candles.py), datetime-indexed.
    Returns a DataFrame, same index, one column per pattern type with a
    score in [-100, 100]. Sign = direction (+bullish, -bearish); doji is
    magnitude-only (direction-neutral, reported unsigned).

    tweezer_tol: relative price tolerance for tweezer matching. Use
    instruments.scale_params(pair)["tweezer_tol_rel"] so JPY pairs and
    indices are not scored with EUR/USD-tuned thresholds.
    """
    n = len(df)
    scores = {col: np.zeros(n) for col in PATTERN_COLUMNS}

    for i in range(n):
        row = df.iloc[i]
        avg_body = _avg_body(df, i, lookback)

        scores["hammer"][i] = _score_hammer(row, avg_body)
        scores["shooting_star"][i] = _score_shooting_star(row, avg_body)
        scores["doji"][i] = _score_doji(row)
        scores["marubozu"][i] = _score_marubozu(row, avg_body)

        if i >= 1:
            prev = df.iloc[i - 1]
            scores["engulfing"][i] = _score_engulfing(prev, row)
            scores["tweezer"][i] = _score_tweezer(prev, row, avg_body, tol=tweezer_tol)

        if i >= 2:
            c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], row
            bull_star = _score_star(c1, c2, c3, avg_body, bullish=True)
            bear_star = _score_star(c1, c2, c3, avg_body, bullish=False)
            scores["star"][i] = bull_star if bull_star != 0 else bear_star
            scores["three_soldiers_crows"][i] = _score_three_soldiers_crows(c1, c2, c3, avg_body)

    result = pd.DataFrame(scores, index=df.index)
    return result


def summarize_signals(df: pd.DataFrame, pattern_df: pd.DataFrame, min_score: float = 40) -> pd.DataFrame:
    """
    Collapse the per-pattern score grid into a tidy list of detected
    signals above a threshold: timestamp, pattern name, direction, score,
    plus the candle's OHLC for reference.
    """
    rows = []
    for col in PATTERN_COLUMNS:
        series = pattern_df[col]
        hits = series[series.abs() >= min_score]
        for ts, score in hits.items():
            candle = df.loc[ts]
            rows.append({
                "timestamp": ts,
                "pattern": col,
                "direction": "bullish" if score > 0 else ("bearish" if score < 0 else "neutral"),
                "score": abs(score),
                "open": candle.Open, "high": candle.High,
                "low": candle.Low, "close": candle.Close,
            })
    out = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return out
