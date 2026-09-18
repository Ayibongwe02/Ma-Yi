"""
Plain-language layer for alerts and dashboard.

Translates engine jargon (pips, SL, TP, raw scores) into short English
someone who does not trade can follow. Confidence bands default to
empirical quantiles from backtest/reports/score_distribution.json when
present; otherwise sensible fallbacks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCORE_DIST_PATH = ROOT / "backtest" / "reports" / "score_distribution.json"

# Fallback bands when no backtest report exists yet
_DEFAULT_BANDS = {"p33": 50.0, "p66": 70.0}

GLOSSARY: dict[str, str] = {
    "pip": (
        "A pip is a tiny step in the price — for most currency pairs it is "
        "the fourth digit after the decimal (0.0001). On yen pairs it is "
        "0.01. On stock indices we talk in 'points' instead."
    ),
    "stop_loss": (
        "Your max-loss point: if the price reaches this level, the idea is "
        "considered wrong and the position is closed to limit the loss."
    ),
    "take_profit": (
        "Your target: if the price reaches this level, the trade is closed "
        "for a gain. It is usually set as a multiple of the amount you were "
        "willing to risk (the distance to the max-loss point)."
    ),
    "risk_reward": (
        "Risk:reward (R:R) compares how much you stand to gain versus how "
        "much you risk. 1.5 means the target is one-and-a-half times farther "
        "from entry than the max-loss point."
    ),
    "confidence": (
        "How strong this setup looks compared with past signals from the "
        "same engine. Weak / Moderate / Strong are labels based on the "
        "score distribution measured in historical backtests — not a "
        "guarantee the trade will win."
    ),
    "pattern": (
        "A candlestick pattern is a short shape on the price chart that "
        "traders have named because it often appears near turning points "
        "or continuations (for example a hammer or an engulfing candle)."
    ),
    "trend": (
        "The general direction price has been moving on a longer clock. "
        "This system prefers setups that agree with that longer direction."
    ),
}


PATTERN_PLAIN: dict[str, str] = {
    "hammer": "a hammer candle (buyers stepped in after a dip)",
    "shooting_star": "a shooting-star candle (sellers pushed back after a rise)",
    "doji": "a doji (buyers and sellers in balance — indecision)",
    "marubozu": "a full-bodied candle (strong one-sided move)",
    "engulfing": "an engulfing pattern (this candle swallowed the previous one)",
    "tweezer": "a tweezer pattern (matching highs or lows on two candles)",
    "star": "a star pattern (pause candle between two stronger moves)",
    "three_soldiers_crows": "three soldiers/crows (three steps in the same direction)",
}


def load_confidence_bands() -> dict[str, float]:
    if SCORE_DIST_PATH.exists():
        try:
            data = json.loads(SCORE_DIST_PATH.read_text())
            if data.get("n", 0) > 0 and "p33" in data and "p66" in data:
                return {"p33": float(data["p33"]), "p66": float(data["p66"])}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return dict(_DEFAULT_BANDS)


def confidence_label(score: float, bands: dict[str, float] | None = None) -> str:
    """Map |score| to Weak / Moderate / Strong using empirical quantiles."""
    bands = bands or load_confidence_bands()
    mag = abs(score)
    if mag < bands["p33"]:
        return "Weak"
    if mag < bands["p66"]:
        return "Moderate"
    return "Strong"


def display_pair(pair: str) -> str:
    try:
        import sys

        data_path = str(ROOT / "data")
        if data_path not in sys.path:
            sys.path.insert(0, data_path)
        from instruments import get_instrument

        return get_instrument(pair).display_name
    except Exception:
        return pair.replace("=X", "").replace("^", "")


def scale_for(pair: str) -> dict[str, Any]:
    try:
        import sys

        data_path = str(ROOT / "data")
        if data_path not in sys.path:
            sys.path.insert(0, data_path)
        from instruments import scale_params

        return scale_params(pair)
    except Exception:
        return {
            "pip_size": 0.0001,
            "price_decimals": 5,
            "approx_usd_per_pip_per_unit": 0.0001,
            "display_name": pair,
            "asset_class": "fx",
        }


def distance_in_pips(entry: float, other: float, pip_size: float) -> float:
    if pip_size <= 0:
        return abs(entry - other)
    return abs(entry - other) / pip_size


def rough_dollar_risk(
    entry: float,
    sl: float,
    pip_size: float,
    approx_usd_per_pip_per_unit: float,
    units: int = 1000,
    equity: float = 10_000.0,
    risk_frac: float = 0.01,
) -> tuple[float, str]:
    """
    Two views of risk:
      1) model risk budget = equity * risk_frac (what Stage 7 aims for)
      2) raw $ if you traded `units` with this stop distance
    Returns (budget_dollars, explanation snippet).
    """
    budget = equity * risk_frac
    pips = distance_in_pips(entry, sl, pip_size)
    unit_label = "points" if pip_size >= 1.0 else "pips"
    # For indices, per-unit $ risk depends on the CFD contract; only quote
    # the account risk budget to avoid misleading unit arithmetic.
    if pip_size >= 1.0:
        note = (
            f"About ${budget:,.0f} at a 1% account risk on a ${equity:,.0f} account "
            f"(stop is ~{pips:.1f} {unit_label} away). Position size should be "
            f"set so that distance equals that budget — not a fixed unit count."
        )
    else:
        raw = pips * approx_usd_per_pip_per_unit * units
        note = (
            f"About ${budget:,.0f} at a 1% account risk on a ${equity:,.0f} account "
            f"(stop is ~{pips:.1f} {unit_label} away). "
            f"At {units} units the same stop is roughly ${raw:,.0f} of price risk."
        )
    return budget, note


def why_this_fired(
    pattern: str,
    direction: int,
    regime: str | None = None,
    sr_score: float | None = None,
    volatility: str | None = None,
) -> str:
    side = "buy" if direction > 0 else "sell"
    pattern_txt = PATTERN_PLAIN.get(pattern, pattern.replace("_", " "))
    bits = [f"A {side} setup from {pattern_txt}"]

    if regime == "with_long_trend":
        bits.append("in line with the longer-term direction")
    elif regime == "counter_trend_early":
        bits.append("against the longer trend, but the short-term turn looks early and strong")
    elif regime == "counter_trend_ignored":
        bits.append("note: it leans against the longer-term direction")
    elif regime == "no_long_trend":
        bits.append("while the longer-term direction is unclear")

    if sr_score is not None and sr_score >= 0.4:
        bits.append("near a recent support/resistance area")
    if volatility == "spike":
        bits.append("(volatility is unusually high — treated cautiously)")
    elif volatility == "quiet":
        bits.append("(the market is unusually quiet)")

    sentence = bits[0]
    for b in bits[1:]:
        if b.startswith("("):
            sentence += f" {b}"
        else:
            sentence += f", {b}"
    return sentence + "."


def format_plain_signal_message(
    pair: str,
    timeframe: str,
    pattern: str,
    direction: int,
    score: float,
    entry: float,
    sl: float,
    tp: float,
    bar_time: str,
    regime: str | None = None,
    sr_score: float | None = None,
    volatility: str | None = None,
    equity: float = 10_000.0,
    risk_frac: float = 0.01,
) -> str:
    """Plain-English Telegram / console alert body (Markdown)."""
    scale = scale_for(pair)
    name = scale.get("display_name") or display_pair(pair)
    side = "Buy opportunity" if direction > 0 else "Sell opportunity"
    label = confidence_label(score)
    pip_size = float(scale.get("pip_size") or 0.0001)
    unit = "points" if pip_size >= 1.0 else "pips"
    risk_pips = distance_in_pips(entry, sl, pip_size)
    reward_pips = distance_in_pips(entry, tp, pip_size)
    rr = reward_pips / risk_pips if risk_pips > 0 else 0.0
    _, risk_note = rough_dollar_risk(
        entry,
        sl,
        pip_size,
        float(scale.get("approx_usd_per_pip_per_unit") or 0.0001),
        equity=equity,
        risk_frac=risk_frac,
    )
    why = why_this_fired(pattern, direction, regime, sr_score, volatility)
    decimals = int(scale.get("price_decimals") or 5)

    return (
        f"📡 *Market signal*\n"
        f"*{side}* on *{name}* ({timeframe} chart)\n\n"
        f"*Why:* {why}\n"
        f"*Confidence:* {label} (engine score {abs(score):.0f}/100)\n\n"
        f"*Suggested plan*\n"
        f"• Enter near `{entry:.{decimals}f}`\n"
        f"• Max-loss point (stop): `{sl:.{decimals}f}` "
        f"(~{risk_pips:.1f} {unit} away)\n"
        f"• Target: `{tp:.{decimals}f}` "
        f"(~{reward_pips:.1f} {unit}, about {rr:.1f}× the risk)\n\n"
        f"_{risk_note}_\n\n"
        f"Bar time: `{bar_time}`\n"
        f"_This is not financial advice. Past patterns do not guarantee future results._"
    )


def glossary_markdown() -> str:
    lines = ["### Glossary", ""]
    titles = {
        "pip": "Pip / point",
        "stop_loss": "Max-loss point (stop)",
        "take_profit": "Target (take profit)",
        "risk_reward": "Risk : reward",
        "confidence": "Confidence",
        "pattern": "Candlestick pattern",
        "trend": "Trend",
    }
    for key, title in titles.items():
        lines.append(f"**{title}** — {GLOSSARY[key]}")
        lines.append("")
    return "\n".join(lines)
