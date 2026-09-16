"""
ML pre-train / bootstrap — generate a large, non-repeating set of closed
trade outcomes (target ~1000) that statistically resemble yfinance-driven
history, persist them in a durable pool, and fit the outcome model.

Why this exists
---------------
Live labelling only accumulates a handful of closed trades per day. With
<30–50 samples GradientBoosting easily memorises (train_acc ≈ 1.0, large
walk-forward gap). The Stage-4 health manager correctly pauses learning,
but the model still needs a bigger, more diverse prior.

This module:
  1. Builds long multi-regime synthetic OHLC series (or uses cached /
     live yfinance data when available).
  2. Runs the same pattern → context → levels → simulate_outcome pipeline
     used live / in backtest.
  3. Writes closed (win/loss) rows to engine/ml/bootstrap_labels.jsonl
     (durable, not mixed into the live signal log).
  4. Calls fit_from_logs so the model starts with a healthy sample size.

The learner automatically merges this pool on every fit (see learner.py).

Usage
-----
  PYTHONPATH=. python -m engine.ml.bootstrap
  PYTHONPATH=. python -m engine.ml.bootstrap --target 1200 --bars 900
  PYTHONPATH=. python -m engine.ml.bootstrap --clear   # wipe pool then regenerate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "delivery"))

BOOTSTRAP_PATH = ROOT / "engine" / "ml" / "bootstrap_labels.jsonl"

DEFAULT_PAIRS = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "GBPJPY=X",
    "^DJI",
]
DEFAULT_TIMEFRAMES = ["1h"]
DEFAULT_TARGET = 1000
DEFAULT_BARS = 900


def _load_bootstrap_rows() -> list[dict]:
    if not BOOTSTRAP_PATH.exists():
        return []
    rows: list[dict] = []
    with open(BOOTSTRAP_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append_bootstrap(rows: list[dict]) -> None:
    BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BOOTSTRAP_PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def clear_bootstrap_pool() -> int:
    n = len(_load_bootstrap_rows())
    if BOOTSTRAP_PATH.exists():
        BOOTSTRAP_PATH.unlink()
    return n


def _count_pool() -> int:
    return sum(
        1
        for r in _load_bootstrap_rows()
        if str(r.get("outcome") or "").lower() in ("win", "loss")
    )


def _load_or_synth(pair: str, timeframe: str, n_bars: int, period: str = "60d"):
    """Prefer real/cached yfinance-style data; fall back to multi-regime synthetic."""
    from candles import fetch_history, load_cached, _save
    from _synthetic import generate_synthetic_candles
    from instruments import get_instrument
    import pandas as pd

    try:
        df = load_cached(pair, timeframe)
        if len(df) >= min(150, n_bars // 3):
            if len(df) < n_bars:
                try:
                    start = float(df["Close"].iloc[-1])
                except Exception:
                    start = float(get_instrument(pair).typical_price)
                extra = generate_synthetic_candles(
                    n=n_bars - len(df) + 5,
                    start_price=start,
                    freq="1h" if timeframe in ("1h", "4h") else timeframe,
                    seed=abs(hash(pair + timeframe + "ext")) % 50_000,
                    regime_mix=True,
                )
                extra = extra.iloc[1:]
                df = pd.concat([df, extra])
                df = df[~df.index.duplicated(keep="first")].sort_index()
            return df, "cached+synth"
    except Exception:
        pass

    try:
        df = fetch_history(pair, timeframe, period=period)
        if len(df) >= 80:
            return df, "yfinance"
    except Exception:
        pass

    try:
        start = float(get_instrument(pair).typical_price)
    except Exception:
        start = 1.0850
    freq = "1h" if timeframe in ("1h", "4h") else timeframe
    df = generate_synthetic_candles(
        n=n_bars,
        start_price=start,
        freq=freq,
        seed=abs(hash(pair + timeframe)) % 50_000 + 17,
        regime_mix=True,
    )
    if timeframe == "4h":
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        df = df.resample("4h").agg(agg).dropna(how="any")
    try:
        _save(df, pair, timeframe)
    except Exception:
        pass
    return df, "synthetic"


def _run_engine_collect(
    df,
    pair: str,
    timeframe: str,
    threshold: float = 40.0,
    min_bars: int = 60,
    start_id: int = 1,
) -> list[dict]:
    """
    Same pipeline as delivery.runner.process_dataframe but:
      - never sends alerts / never touches live signal log
      - returns closed trade dicts ready for the bootstrap pool
    """
    from patterns import detect_patterns, PATTERN_COLUMNS
    from context import apply_context_filters
    from trade_params import compute_trade_levels, simulate_outcome
    from instruments import scale_params
    from delivery.runner import _best_pattern, COUNTER_TREND_POLICY

    if len(df) < min_bars:
        return []

    scale = scale_params(pair)
    pattern_df = detect_patterns(df, tweezer_tol=scale["tweezer_tol_rel"])
    context_df = apply_context_filters(
        df,
        pattern_df,
        counter_trend_policy=COUNTER_TREND_POLICY,
        round_increment=scale["round_increment"],
    )

    closed: list[dict] = []
    sid = start_id
    for i in range(min_bars, len(df)):
        row = context_df.iloc[i]
        pattern, score = _best_pattern(row, PATTERN_COLUMNS)
        if pattern == "" or abs(score) < 1e-6:
            continue

        direction = 1 if score > 0 else -1
        if abs(score) < threshold:
            continue

        trend = int(row.get(f"{pattern}_trend", 0) or 0)
        short_trend = int(row.get(f"{pattern}_short_trend", 0) or 0)
        regime = str(row.get(f"{pattern}_regime", "") or "")
        structure = str(row.get(f"{pattern}_structure", "") or "")
        zone = str(row.get(f"{pattern}_zone", "") or "")
        sr = float(row.get(f"{pattern}_sr", 0) or 0)
        vol = str(row.get(f"{pattern}_vol", "normal") or "normal")
        raw = float(pattern_df[pattern].iloc[i]) if pattern in pattern_df.columns else None

        levels = compute_trade_levels(
            df, i, direction, price_decimals=scale["price_decimals"]
        )
        outcome = simulate_outcome(df, i, direction, levels["sl"], levels["tp"])
        if outcome not in ("win", "loss"):
            continue

        bar_time = df.index[i]
        bar_ts = bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time)

        closed.append(
            {
                "id": sid,
                "ts": bar_ts,
                "pair": pair,
                "timeframe": timeframe,
                "bar_time": bar_ts,
                "pattern": pattern,
                "direction": int(direction),
                "raw_score": raw,
                "final_score": float(score),
                "fired": True,
                "entry": levels["entry"],
                "sl": levels["sl"],
                "tp": levels["tp"],
                "risk": levels["risk"],
                "rr": levels["rr"],
                "trend": trend,
                "sr_score": sr,
                "volatility": vol,
                "outcome": outcome,
                "alerted": False,
                "meta": {
                    "short_trend": short_trend,
                    "regime": regime,
                    "counter_trend_policy": COUNTER_TREND_POLICY,
                    "structure": structure,
                    "zone": zone,
                    "bootstrap": True,
                    "seed": "bootstrap",
                },
                "created_at": bar_ts,
            }
        )
        sid += 1
    return closed


def bootstrap(
    pairs: list[str] | None = None,
    timeframes: list[str] | None = None,
    target_samples: int = DEFAULT_TARGET,
    n_bars: int = DEFAULT_BARS,
    threshold: float = 40.0,
    clear_previous: bool = False,
    fit: bool = True,
) -> dict[str, Any]:
    """
    Generate enough closed labeled trades to reach `target_samples` in the
    durable bootstrap pool, then optionally fit the ML model.
    """
    pairs = pairs or DEFAULT_PAIRS
    timeframes = timeframes or DEFAULT_TIMEFRAMES

    if clear_previous:
        removed = clear_bootstrap_pool()
        print(f"Cleared {removed} previous bootstrap rows.")

    before = _count_pool()
    print(f"Bootstrap pool size before: {before}")
    print(f"Target: ≥{target_samples} | pairs={pairs} | tfs={timeframes} | bars≈{n_bars}")

    all_closed: list[dict] = []
    sources: dict[str, str] = {}
    next_id = before + 1

    for pair in pairs:
        if _count_pool() + len(all_closed) >= target_samples:
            print(f"Reached target ({target_samples}) — stopping early.")
            break
        for tf in timeframes:
            if _count_pool() + len(all_closed) >= target_samples:
                break
            print(f"→ {pair} @ {tf} …", end=" ", flush=True)
            try:
                df, src = _load_or_synth(pair, tf, n_bars=n_bars)
                sources[f"{pair}|{tf}"] = src
                closed = _run_engine_collect(
                    df, pair, tf, threshold=threshold, start_id=next_id
                )
            except Exception as exc:
                print(f"FAILED ({exc.__class__.__name__}: {exc})")
                continue
            next_id += len(closed)
            all_closed.extend(closed)
            # Persist incrementally so a later pair failure does not lose work
            if closed:
                _append_bootstrap(closed)
            wins = sum(1 for c in closed if c["outcome"] == "win")
            losses = len(closed) - wins
            print(f"{len(df)} bars ({src}) → {len(closed)} closed ({wins}W/{losses}L)")

    after = _count_pool()
    print(f"\nBootstrap pool size after: {after} (+{after - before})")

    result: dict[str, Any] = {
        "ok": True,
        "before": before,
        "after": after,
        "added": after - before,
        "target": target_samples,
        "sources": sources,
        "n_closed_this_run": len(all_closed),
        "pool_path": str(BOOTSTRAP_PATH),
    }

    if fit and after >= 12:
        print("\nFitting ML model on live labels + bootstrap pool…")
        from engine.ml.learner import get_learner

        learner = get_learner()
        fit_result = learner.maybe_retrain(reason="bootstrap", force=True, min_samples=12)
        result["fit"] = fit_result
        if fit_result.get("ok"):
            h = fit_result.get("health") or {}
            print(
                f"  n_samples={fit_result.get('n_samples')}  "
                f"train_acc={fit_result.get('train_accuracy')}  "
                f"walk_forward={fit_result.get('walk_forward', {}).get('accuracy')}  "
                f"health={h.get('state')}"
            )
        else:
            print(f"  Fit skipped/failed: {fit_result.get('reason')}")
    elif not fit:
        result["fit"] = {"skipped": True, "reason": "fit=False"}
    else:
        result["fit"] = {"ok": False, "reason": f"only {after} samples (need ≥12)"}

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ma-yi ML bootstrap / pre-train")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Desired closed labeled sample count in pool")
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS, help="Synthetic / history bars per pair")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS), help="Comma-separated tickers")
    parser.add_argument("--timeframes", default="1h", help="Comma-separated timeframes")
    parser.add_argument("--threshold", type=float, default=40.0)
    parser.add_argument("--clear", action="store_true", help="Wipe bootstrap pool first")
    parser.add_argument("--no-fit", action="store_true", help="Only generate labels, do not fit model")
    args = parser.parse_args()

    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    result = bootstrap(
        pairs=pairs,
        timeframes=tfs,
        target_samples=args.target,
        n_bars=args.bars,
        threshold=args.threshold,
        clear_previous=args.clear,
        fit=not args.no_fit,
    )
    print("\nDone. Pool size:", result.get("after"))
    if result.get("fit", {}).get("ok"):
        print("Model trained. Health state:", (result["fit"].get("health") or {}).get("state"))


if __name__ == "__main__":
    main()
