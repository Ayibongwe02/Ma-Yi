"""
Tests for the Stage 3 market-structure and supply/demand zone additions to
engine/context.py. Uses small, deterministic synthetic OHLC series (not
real market data) so every assertion is exact and reproducible.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from context import (  # noqa: E402
    apply_context_filters, compute_atr, compute_market_structure,
    compute_zones, zone_confluence, _structure_multiplier, _zone_multiplier, _zone_label,
)
from patterns import detect_patterns  # noqa: E402


def _mk_df(rows) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])


def _quiet_bar():
    return [1.1000, 1.1004, 1.0996, 1.1000]  # O, H, L, C — range 0.0008, closes flat


def _impulse_and_retest_df(with_retest: bool):
    """20 quiet bars (ATR settles at 0.0008), then a strong bullish impulse
    (body ~0.006, well over 1.6x ATR) — the base candle (bar 19) should
    become a demand zone at [1.0996, 1.1004]. Optionally add a retest bar
    right after that dips back into the zone."""
    rows = [_quiet_bar() for _ in range(20)]
    rows.append([1.1000, 1.1062, 1.0998, 1.1060])  # impulse bar (idx 20)
    if with_retest:
        rows.append([1.1060, 1.1065, 1.0998, 1.1050])  # retest bar (idx 21) — Low dips into zone
    for _ in range(10):
        rows.append([1.1060, 1.1064, 1.1056, 1.1060])  # drift away, never near the zone again
    return _mk_df(rows)


def test_compute_zones_detects_demand_zone_from_bullish_impulse():
    df = _impulse_and_retest_df(with_retest=False)
    atr = compute_atr(df, period=14)
    demand, supply = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    assert len(demand) == 1
    z = demand[0]
    assert z["formed_at"] == 19
    assert z["impulse_at"] == 20
    assert abs(z["top"] - 1.1004) < 1e-9
    assert abs(z["bottom"] - 1.0996) < 1e-9
    assert z["first_test"] is None  # never retested in this dataset
    assert supply == []


def test_compute_zones_marks_first_test_when_price_returns():
    df = _impulse_and_retest_df(with_retest=True)
    atr = compute_atr(df, period=14)
    demand, _ = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    assert demand[0]["first_test"] == 21


def test_zone_confluence_fresh_when_untested():
    df = _impulse_and_retest_df(with_retest=False)
    atr = compute_atr(df, period=14)
    demand, supply = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    score, agrees, fresh = zone_confluence(demand, supply, i=25, direction=1, price=1.1000)
    assert agrees is True
    assert fresh is True
    assert score == 1.0


def test_zone_confluence_stale_after_retest():
    df = _impulse_and_retest_df(with_retest=True)
    atr = compute_atr(df, period=14)
    demand, supply = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    # Scoring at a bar well after the retest (idx 21) happened
    score, agrees, fresh = zone_confluence(demand, supply, i=28, direction=1, price=1.1000)
    assert agrees is True
    assert fresh is False
    assert score == 0.6


def test_zone_confluence_not_yet_tested_before_the_retest_bar():
    # Same dataset, but scoring at a bar *before* the retest happened — the
    # zone must still read as fresh, since that test hasn't occurred yet
    # from this bar's point of view (no lookahead).
    df = _impulse_and_retest_df(with_retest=True)
    atr = compute_atr(df, period=14)
    demand, supply = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    score, agrees, fresh = zone_confluence(demand, supply, i=21, direction=1, price=1.1000)
    assert fresh is True  # first_test (21) is not strictly < i (21)


def test_zone_confluence_opposing_zone_is_a_headwind():
    df = _impulse_and_retest_df(with_retest=False)
    atr = compute_atr(df, period=14)
    demand, supply = compute_zones(df, atr, impulse_atr_mult=1.6, max_zone_age=150)
    # A bearish pattern (direction=-1) sitting inside a demand zone (the
    # "opposite" side for it) should read as a headwind, not confluence.
    score, agrees, fresh = zone_confluence(demand, supply, i=25, direction=-1, price=1.1000)
    assert agrees is False
    assert score == 0.5


def test_zone_multiplier_and_label_table():
    assert _zone_multiplier(1.0, True, True) == 1.25
    assert _zone_multiplier(0.6, True, False) == 1.10
    assert _zone_multiplier(0.5, False, False) == 0.65
    assert _zone_multiplier(0.0, False, False) == 1.0

    assert _zone_label(1.0, True, True, direction=1) == "demand_fresh"
    assert _zone_label(0.6, True, False, direction=1) == "demand_tested"
    assert _zone_label(1.0, True, True, direction=-1) == "supply_fresh"
    assert _zone_label(0.5, False, False, direction=1) == "opposing_zone"
    assert _zone_label(0.0, False, False, direction=1) == "none"


def test_market_structure_bos_and_choch():
    # Hand-crafted swing sequence: higher high (idx2->idx8) + higher low
    # (idx4->idx10) => bullish structure once both are confirmed (i > 10).
    swing_highs = [(2, 1.1000), (8, 1.1050)]
    swing_lows = [(4, 1.0950), (10, 1.0980)]
    rows = []
    closes = {11: 1.1060, 13: 1.0970}  # 11 breaks above h2 (BOS), 13 breaks below l2 (CHoCH)
    for i in range(15):
        c = closes.get(i, 1.1010)
        rows.append([c, c + 0.0005, c - 0.0005, c])
    df = _mk_df(rows)

    events = compute_market_structure(df, swing_highs, swing_lows)
    assert events.iloc[11] == "bullish_bos"
    assert events.iloc[13] == "bearish_choch"
    # Before both swing extremes are confirmed, there should be no event
    assert events.iloc[5] == ""


def test_structure_multiplier_agreement_table():
    assert _structure_multiplier("bullish_bos", direction=1) == 1.20
    assert _structure_multiplier("bullish_choch", direction=1) == 1.10
    assert _structure_multiplier("bearish_choch", direction=1) == 0.55  # headwind
    assert _structure_multiplier("bearish_bos", direction=1) == 0.75
    assert _structure_multiplier("", direction=1) == 1.0
    # mirror for bearish patterns
    assert _structure_multiplier("bearish_bos", direction=-1) == 1.20
    assert _structure_multiplier("bullish_choch", direction=-1) == 0.55


def test_apply_context_filters_exposes_structure_and_zone_columns():
    df = _impulse_and_retest_df(with_retest=True)
    pattern_df = detect_patterns(df)
    context_df = apply_context_filters(df, pattern_df)
    for col in pattern_df.columns:
        assert f"{col}_structure" in context_df.columns
        assert f"{col}_zone" in context_df.columns
        # scores stay in bounds regardless of how many multipliers stacked
        assert context_df[col].abs().max() <= 100.0
        zone_vals = set(context_df[f"{col}_zone"].unique())
        assert zone_vals <= {"", "none", "demand_fresh", "demand_tested",
                              "supply_fresh", "supply_tested", "opposing_zone"}


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"  ok: {t.__name__}")
    print(f"\n{passed}/{len(tests)} structure/zone tests passed.")
