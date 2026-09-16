"""Lightweight multi-TF bias + structure/zone + COT divergence snapshots.

Used by Live Command so the desk can show direction without a full rescan.
Heavy work is cached; call refresh_pair_bias() after /api/scan.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "engine"))

CACHE_PATH = ROOT / "delivery" / "logs" / "pair_bias.json"

WATCH_LIST = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GBPJPY=X", "^DJI"]
WATCH_LABELS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "GBPJPY=X": "GBP/JPY",
    "^DJI": "US30",
}


def _label(pair: str) -> str:
    return WATCH_LABELS.get(pair, pair)


def _trend_name(v: int) -> str:
    if v > 0:
        return "bullish"
    if v < 0:
        return "bearish"
    return "neutral"


def _compute_one(pair: str, timeframe: str = "1h") -> dict[str, Any]:
    out: dict[str, Any] = {
        "pair": pair,
        "label": _label(pair),
        "timeframe": timeframe,
        "htf_bias": "neutral",
        "stf_bias": "neutral",
        "aligned": False,
        "structure": "",
        "zone": "",
        "regime": "",
        "cot_score": None,
        "sentiment_score": None,
        "divergence": None,
        "divergence_label": "",
        "error": None,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from candles import fetch_history
        from context import (
            compute_htf_trend,
            compute_same_tf_trend,
            compute_swing_levels,
            compute_market_structure,
            compute_zones,
            zone_confluence,
        )
        import pandas as pd

        df = fetch_history(pair, timeframe=timeframe, period="30d")
        if df is None or len(df) < 60:
            out["error"] = "insufficient bars"
            return out

        stf = compute_same_tf_trend(df)
        htf = compute_htf_trend(df, htf_rule="4h" if timeframe in ("1h", "15m") else "1D")
        i = len(df) - 1
        stf_v = int(stf.iloc[i]) if stf is not None and len(stf) else 0
        htf_v = int(htf.iloc[i]) if htf is not None and len(htf) else 0
        out["stf_bias"] = _trend_name(stf_v)
        out["htf_bias"] = _trend_name(htf_v)
        out["aligned"] = stf_v != 0 and stf_v == htf_v

        try:
            sh, sl = compute_swing_levels(df)
            events = compute_market_structure(df, sh, sl)
            # last non-empty structure event in last 20 bars
            for j in range(i, max(-1, i - 20), -1):
                ev = str(events.iloc[j] or "")
                if ev:
                    out["structure"] = ev
                    break
        except Exception:
            pass

        try:
            from context import _atr_series  # may not exist
        except Exception:
            _atr_series = None
        try:
            # ATR via simple true range
            high, low, close = df["High"], df["Low"], df["Close"]
            prev_close = close.shift(1)
            tr = pd.concat(
                [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
                axis=1,
            ).max(axis=1)
            atr = tr.rolling(14).mean()
            demand, supply = compute_zones(df, atr)
            price = float(close.iloc[i])
            # direction-agnostic: report nearest same-side labels for both
            z_long, agrees_l, fresh_l = zone_confluence(demand, supply, i, 1, price)
            z_short, agrees_s, fresh_s = zone_confluence(demand, supply, i, -1, price)
            if agrees_l:
                out["zone"] = "demand_fresh" if fresh_l else "demand_tested"
            elif agrees_s:
                out["zone"] = "supply_fresh" if fresh_s else "supply_tested"
            elif z_long > 0 or z_short > 0:
                out["zone"] = "near_opposing"
            else:
                out["zone"] = "none"
        except Exception as e:
            out["zone"] = ""
            out["error"] = (out.get("error") or "") + f" zone:{e}"

        # COT + optional sentiment (best-effort, live only)
        cot_score = None
        sent_score = None
        try:
            from cot import fetch_cot_score, CotUnavailable
            cot_score = fetch_cot_score(pair)
            out["cot_score"] = cot_score
        except Exception:
            cot_score = None
        try:
            from sentiment import fetch_sentiment_score, SentimentUnavailable
            sent_score = fetch_sentiment_score(pair)
            out["sentiment_score"] = sent_score
        except Exception:
            sent_score = None

        if cot_score is not None and sent_score is not None:
            try:
                from confidence import retail_institutional_divergence
                div = int(retail_institutional_divergence(sent_score, cot_score))
                out["divergence"] = div
                if div == 1:
                    out["divergence_label"] = "confirmed"  # smart money agrees with fade
                elif div == -1:
                    out["divergence_label"] = "conflict"
                else:
                    out["divergence_label"] = "neutral"
            except Exception:
                pass
        elif cot_score is not None:
            out["divergence_label"] = "cot_only"
    except Exception as e:
        out["error"] = str(e)
    return out


def refresh_pair_bias(
    pairs: Optional[list[str]] = None,
    timeframe: str = "1h",
) -> list[dict[str, Any]]:
    pairs = pairs or list(WATCH_LIST)
    rows = [_compute_one(p, timeframe=timeframe) for p in pairs]
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "rows": rows}, indent=2)
    )
    return rows


def load_pair_bias() -> list[dict[str, Any]]:
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text())
        return data.get("rows") or []
    except Exception:
        return []
