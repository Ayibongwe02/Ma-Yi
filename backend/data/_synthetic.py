"""
Synthetic OHLC generator for offline / pre-training use.

Produces realistic multi-regime candle series (trend, range, high/low vol)
that approximate the statistical shape of yfinance FX/index data without
hitting the network. Used by:
  - backtest fallback when live fetch fails
  - ML bootstrap / pre-train pipeline (engine/ml/bootstrap.py)

This is intentional infrastructure, not a temporary sandbox stub.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def generate_synthetic_candles(
    n: int = 200,
    start_price: float = 1.0850,
    freq: str = "1h",
    seed: int = 42,
    regime_mix: bool = True,
) -> pd.DataFrame:
    """
    Generate OHLC candles.

    When regime_mix=True (default), the series is built from alternating
    regimes (bull trend, bear trend, range, high-vol spike, quiet) so the
    pattern engine sees non-repeating structure — critical for reducing
    ML overfitting on small homogeneous samples.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(end=pd.Timestamp.utcnow().floor("h"), periods=n, freq=freq)

    if not regime_mix or n < 80:
        # Simple GBM fallback (legacy behaviour) — relative vol only
        vol = 0.0006 if start_price < 50 else 0.0005
        returns = np.clip(rng.normal(0, vol, n), -0.04, 0.04)
        closes = start_price * (1 + returns).cumprod()
    else:
        closes = _regime_path(rng, n, start_price)

    opens = np.roll(closes, 1)
    opens[0] = start_price
    # Intrabar noise scales with price level
    noise_scale = max(1e-5, abs(start_price) * 0.00025)
    highs = np.maximum(opens, closes) + np.abs(rng.normal(0, noise_scale, n))
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0, noise_scale, n))
    # Occasional larger wicks (pin-bar / spike material)
    spike_mask = rng.random(n) < 0.04
    highs[spike_mask] += np.abs(rng.normal(0, noise_scale * 4, spike_mask.sum()))
    lows[spike_mask] -= np.abs(rng.normal(0, noise_scale * 4, spike_mask.sum()))
    volumes = rng.integers(80, 1200, n).astype(float)
    volumes[spike_mask] *= rng.uniform(1.5, 3.0, spike_mask.sum())

    df = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )
    df.index.name = "Datetime"
    return df


def _regime_path(rng: np.random.Generator, n: int, start: float) -> np.ndarray:
    """Piecewise regimes so patterns and outcomes are not pure noise."""
    closes = np.empty(n)
    price = float(start)
    i = 0
    # Relative return vol (fraction of price) — same order of magnitude for FX and indices
    base_vol = 0.00055 if start < 50 else 0.00045

    regimes = [
        ("bull", 0.00012, 0.7),
        ("bear", -0.00012, 0.7),
        ("range", 0.0, 0.55),
        ("high_vol", 0.0, 1.8),
        ("quiet", 0.0, 0.35),
        ("bull_strong", 0.00022, 0.9),
        ("bear_strong", -0.00022, 0.9),
    ]

    while i < n:
        name, drift, vol_mult = regimes[int(rng.integers(0, len(regimes)))]
        length = int(rng.integers(24, 90))  # ~1–4 days of 1h bars
        length = min(length, n - i)
        vol = base_vol * vol_mult
        for _ in range(length):
            ret = drift + rng.normal(0, vol)
            # mild mean-reversion in range regimes
            if name == "range":
                ret -= 0.08 * (price - start) / max(abs(start), 1e-9)
            ret = float(np.clip(ret, -0.04, 0.04))
            price = price * (1.0 + ret)
            # hard bounds so FX / index series stay realistic (no inf / NaN)
            lo, hi = start * 0.55, start * 1.80
            if not np.isfinite(price) or price < lo or price > hi:
                price = float(np.clip(price if np.isfinite(price) else start, lo, hi))
            closes[i] = price
            i += 1
            if i >= n:
                break
    return closes


def generate_multi_pair_history(
    pairs: list[str],
    n_bars: int = 1200,
    freq: str = "1h",
    base_seed: int = 2026,
) -> dict[str, pd.DataFrame]:
    """
    Produce independent synthetic histories for several instruments.
    Different seeds + typical prices keep series unrepeated across pairs.
    """
    from instruments import get_instrument, scale_params  # local import

    out: dict[str, pd.DataFrame] = {}
    for i, pair in enumerate(pairs):
        try:
            inst = get_instrument(pair)
            start = float(inst.typical_price)
        except Exception:
            start = 1.0850
        seed = base_seed + i * 997 + abs(hash(pair)) % 10_000
        out[pair] = generate_synthetic_candles(
            n=n_bars, start_price=start, freq=freq, seed=seed, regime_mix=True
        )
    return out
