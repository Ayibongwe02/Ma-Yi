"""
Trade parameter calculation for signals.

Given a bar index, direction, and OHLC context, compute:
  - entry  (close of the signal bar)
  - stop loss (ATR-based or recent swing)
  - take profit (fixed R:R multiple of risk)

Used by delivery (live alerts) and the simple outcome simulator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from context import compute_atr, compute_swing_levels


def compute_trade_levels(
    df: pd.DataFrame,
    i: int,
    direction: int,
    atr_period: int = 14,
    atr_sl_mult: float = 1.5,
    rr: float = 1.5,
    use_swing: bool = True,
    swing_window: int = 3,
    swing_lookback: int = 20,
    price_decimals: int = 5,
) -> dict:
    """
    direction: +1 long, -1 short.
    Returns dict with entry, sl, tp, risk (price distance), rr.
    """
    if i < 0 or i >= len(df):
        raise IndexError(f"bar index {i} out of range")

    row = df.iloc[i]
    entry = float(row.Close)
    atr = compute_atr(df, atr_period)
    atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else float(row.High - row.Low) or 0.001

    sl_distance = atr_val * atr_sl_mult

    if use_swing and i >= swing_window:
        swing_highs, swing_lows = compute_swing_levels(df, swing_window)
        start = max(0, i - swing_lookback)
        if direction > 0:
            # long: SL below nearest recent swing low (or ATR fallback)
            candidates = [price for idx, price in swing_lows if start <= idx < i]
            if candidates:
                below = [c for c in candidates if c < entry]
                nearest_low = max(below) if below else min(candidates)
                swing_sl = nearest_low - atr_val * 0.1  # tiny buffer
                if entry - swing_sl < atr_val * 2.5 and entry - swing_sl > atr_val * 0.4:
                    sl_distance = entry - swing_sl
        else:
            candidates = [price for idx, price in swing_highs if start <= idx < i]
            if candidates:
                above = [c for c in candidates if c > entry]
                nearest_high = min(above) if above else max(candidates)
                swing_sl = nearest_high + atr_val * 0.1
                if swing_sl - entry < atr_val * 2.5 and swing_sl - entry > atr_val * 0.4:
                    sl_distance = swing_sl - entry

    if direction > 0:
        sl = entry - sl_distance
        tp = entry + sl_distance * rr
    else:
        sl = entry + sl_distance
        tp = entry - sl_distance * rr

    d = max(0, int(price_decimals))
    return {
        "entry": round(entry, d),
        "sl": round(sl, d),
        "tp": round(tp, d),
        "risk": round(sl_distance, d),
        "rr": rr,
        "direction": direction,
    }


def simulate_outcome(
    df: pd.DataFrame,
    i: int,
    direction: int,
    sl: float,
    tp: float,
    max_bars: int = 48,
) -> str:
    """
    Walk forward from bar i+1 and see whether TP or SL is hit first.
    Returns 'win', 'loss', or 'pending' (if neither hit within max_bars / end of data).
    """
    n = len(df)
    for j in range(i + 1, min(n, i + 1 + max_bars)):
        bar = df.iloc[j]
        if direction > 0:
            if bar.Low <= sl:
                return "loss"
            if bar.High >= tp:
                return "win"
        else:
            if bar.High >= sl:
                return "loss"
            if bar.Low <= tp:
                return "win"
    return "pending"
