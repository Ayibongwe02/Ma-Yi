"""
Real backtest engine for Ma-yi signals.

Runs the same pattern → context → trade-levels pipeline used live, but:
  - never applies live sentiment (historical bars have no past sentiment)
  - walks forward to resolve TP/SL outcomes
  - supports walk-forward splits, parameter sweeps, and Monte Carlo drawdown

Usage (library):
    from backtest.engine import run_backtest, parameter_sweep, walk_forward, monte_carlo
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "engine"))

from instruments import scale_params, get_instrument  # noqa: E402
from patterns import detect_patterns, PATTERN_COLUMNS  # noqa: E402
from context import apply_context_filters  # noqa: E402
from trade_params import compute_trade_levels, simulate_outcome  # noqa: E402


@dataclass
class BacktestConfig:
    threshold: float = 40.0
    atr_sl_mult: float = 1.5
    rr: float = 1.5
    max_bars: int = 48
    min_bars: int = 60
    counter_trend_policy: str = "ignore_short"
    sr_tol_frac: float = 0.0012
    # If True, skip bars where final |score| < threshold (only fired trades)
    fired_only: bool = True


@dataclass
class TradeRecord:
    pair: str
    timeframe: str
    bar_time: str
    pattern: str
    direction: int
    score: float
    entry: float
    sl: float
    tp: float
    risk: float
    rr: float
    outcome: str  # win | loss | pending
    bars_held: int = 0
    pnl_r: float = 0.0  # +rr on win, -1 on loss, 0 pending


@dataclass
class BacktestResult:
    pair: str
    timeframe: str
    config: dict
    trades: list[TradeRecord] = field(default_factory=list)
    n_bars: int = 0

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([asdict(t) for t in self.trades])

    def summary(self) -> dict[str, Any]:
        df = self.to_frame()
        closed = df[df["outcome"].isin(["win", "loss"])] if not df.empty else df
        n = len(closed)
        wins = int((closed["outcome"] == "win").sum()) if n else 0
        losses = int((closed["outcome"] == "loss").sum()) if n else 0
        pending = int((df["outcome"] == "pending").sum()) if not df.empty else 0
        win_rate = (wins / n * 100.0) if n else 0.0
        avg_win = float(closed.loc[closed["outcome"] == "win", "pnl_r"].mean()) if wins else 0.0
        avg_loss = float(closed.loc[closed["outcome"] == "loss", "pnl_r"].mean()) if losses else 0.0
        expectancy = float(closed["pnl_r"].mean()) if n else 0.0
        total_r = float(closed["pnl_r"].sum()) if n else 0.0
        return {
            "pair": self.pair,
            "timeframe": self.timeframe,
            "n_bars": self.n_bars,
            "n_trades": len(df),
            "n_closed": n,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "win_rate_pct": round(win_rate, 1),
            "avg_win_r": round(avg_win, 3),
            "avg_loss_r": round(avg_loss, 3),
            "expectancy_r": round(expectancy, 3),
            "total_r": round(total_r, 2),
            "config": self.config,
        }


def _load_candles(pair: str, timeframe: str, period: str) -> pd.DataFrame:
    from candles import fetch_history, _save
    from instruments import scale_params as _scale

    try:
        return fetch_history(pair, timeframe, period=period)
    except Exception as e:
        print(f"  [{pair}] live fetch failed ({e.__class__.__name__}) — synthetic fallback")
        from _synthetic import generate_synthetic_candles

        scale = _scale(pair)
        n = 280 if timeframe in ("1h", "15m") else 200
        freq = "1h" if timeframe in ("1h", "4h") else timeframe
        df = generate_synthetic_candles(
            n=n, start_price=scale["typical_price"], freq=freq, seed=abs(hash(pair)) % 10_000
        )
        if timeframe == "4h":
            agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            df = df.resample("4h").agg(agg).dropna(how="any")
        _save(df, pair, timeframe)
        return df


def run_backtest(
    pair: str,
    timeframe: str,
    df: pd.DataFrame | None = None,
    period: str | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Full pipeline backtest on one pair/timeframe.

    If df is None, fetches (or synthesises) history for `period`.
    Sentiment is never applied.
    """
    cfg = config or BacktestConfig()
    if df is None:
        if period is None:
            period = "60d" if timeframe in ("1h", "15m") else "730d"
            # yfinance intraday ceiling ~60d; 4h is resampled from 1h so same limit.
            if timeframe == "4h":
                period = "60d"  # realistic yfinance limit; flag longer needs OANDA/TwelveData
        df = _load_candles(pair, timeframe, period)

    scale = scale_params(pair)
    pattern_df = detect_patterns(df, tweezer_tol=scale["tweezer_tol_rel"])
    context_df = apply_context_filters(
        df,
        pattern_df,
        counter_trend_policy=cfg.counter_trend_policy,
        round_increment=scale["round_increment"],
        sr_tol_frac=cfg.sr_tol_frac,
    )

    trades: list[TradeRecord] = []
    for i in range(cfg.min_bars, len(df)):
        row = context_df.iloc[i]
        best_name, best_score = "", 0.0
        for col in PATTERN_COLUMNS:
            val = float(row.get(col, 0) or 0)
            if abs(val) > abs(best_score):
                best_name, best_score = col, val
        if best_name == "" or abs(best_score) < 1e-9:
            continue
        if cfg.fired_only and abs(best_score) < cfg.threshold:
            continue

        direction = 1 if best_score > 0 else -1
        levels = compute_trade_levels(
            df,
            i,
            direction,
            atr_sl_mult=cfg.atr_sl_mult,
            rr=cfg.rr,
            price_decimals=scale["price_decimals"],
        )
        outcome = simulate_outcome(
            df, i, direction, levels["sl"], levels["tp"], max_bars=cfg.max_bars
        )
        # Approximate bars held
        bars_held = 0
        pnl_r = 0.0
        if outcome == "win":
            pnl_r = cfg.rr
            for j in range(i + 1, min(len(df), i + 1 + cfg.max_bars)):
                bars_held += 1
                bar = df.iloc[j]
                hit = (direction > 0 and bar.High >= levels["tp"]) or (
                    direction < 0 and bar.Low <= levels["tp"]
                )
                if hit:
                    break
        elif outcome == "loss":
            pnl_r = -1.0
            for j in range(i + 1, min(len(df), i + 1 + cfg.max_bars)):
                bars_held += 1
                bar = df.iloc[j]
                hit = (direction > 0 and bar.Low <= levels["sl"]) or (
                    direction < 0 and bar.High >= levels["sl"]
                )
                if hit:
                    break

        bar_time = df.index[i]
        trades.append(
            TradeRecord(
                pair=pair,
                timeframe=timeframe,
                bar_time=bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
                pattern=best_name,
                direction=direction,
                score=float(best_score),
                entry=levels["entry"],
                sl=levels["sl"],
                tp=levels["tp"],
                risk=levels["risk"],
                rr=cfg.rr,
                outcome=outcome,
                bars_held=bars_held,
                pnl_r=pnl_r,
            )
        )

    return BacktestResult(
        pair=pair,
        timeframe=timeframe,
        config={
            "threshold": cfg.threshold,
            "atr_sl_mult": cfg.atr_sl_mult,
            "rr": cfg.rr,
            "max_bars": cfg.max_bars,
            "counter_trend_policy": cfg.counter_trend_policy,
            "sr_tol_frac": cfg.sr_tol_frac,
            "tweezer_tol_rel": scale["tweezer_tol_rel"],
            "round_increment": scale["round_increment"],
        },
        trades=trades,
        n_bars=len(df),
    )


def per_pattern_breakdown(result: BacktestResult) -> pd.DataFrame:
    df = result.to_frame()
    if df.empty:
        return pd.DataFrame()
    closed = df[df["outcome"].isin(["win", "loss"])]
    rows = []
    for pattern, g in closed.groupby("pattern"):
        n = len(g)
        wins = int((g["outcome"] == "win").sum())
        rows.append(
            {
                "pair": result.pair,
                "timeframe": result.timeframe,
                "pattern": pattern,
                "n": n,
                "wins": wins,
                "losses": n - wins,
                "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
                "expectancy_r": round(float(g["pnl_r"].mean()), 3) if n else 0.0,
                "total_r": round(float(g["pnl_r"].sum()), 2),
            }
        )
    return pd.DataFrame(rows).sort_values("expectancy_r", ascending=False).reset_index(drop=True)


def walk_forward(
    pair: str,
    timeframe: str,
    df: pd.DataFrame | None = None,
    period: str | None = None,
    train_frac: float = 0.6,
    base_config: BacktestConfig | None = None,
    atr_sl_grid: Iterable[float] = (1.0, 1.5, 2.0),
    rr_grid: Iterable[float] = (1.0, 1.5, 2.0),
) -> dict[str, Any]:
    """
    Tune atr_sl_mult / rr on the first train_frac of bars; evaluate on the hold-out tail.
    Returns best in-sample params and out-of-sample summary.
    """
    if df is None:
        period = period or ("60d" if timeframe in ("1h", "15m", "4h") else "2y")
        df = _load_candles(pair, timeframe, period)

    split = max(int(len(df) * train_frac), (base_config or BacktestConfig()).min_bars + 20)
    train_df = df.iloc[:split].copy()
    test_df = df.iloc[split:].copy()
    if len(test_df) < 30:
        return {
            "pair": pair,
            "timeframe": timeframe,
            "error": "insufficient bars for walk-forward hold-out",
            "n_bars": len(df),
            "split": split,
        }

    base = base_config or BacktestConfig()
    best_expectancy = -1e9
    best_params = {"atr_sl_mult": base.atr_sl_mult, "rr": base.rr}
    train_rows = []

    for atr_m in atr_sl_grid:
        for rr in rr_grid:
            cfg = BacktestConfig(
                threshold=base.threshold,
                atr_sl_mult=atr_m,
                rr=rr,
                max_bars=base.max_bars,
                min_bars=base.min_bars,
                counter_trend_policy=base.counter_trend_policy,
                sr_tol_frac=base.sr_tol_frac,
            )
            res = run_backtest(pair, timeframe, df=train_df, config=cfg)
            s = res.summary()
            train_rows.append({**s, "atr_sl_mult": atr_m, "rr": rr})
            if s["n_closed"] >= 3 and s["expectancy_r"] > best_expectancy:
                best_expectancy = s["expectancy_r"]
                best_params = {"atr_sl_mult": atr_m, "rr": rr}

    oos_cfg = BacktestConfig(
        threshold=base.threshold,
        atr_sl_mult=best_params["atr_sl_mult"],
        rr=best_params["rr"],
        max_bars=base.max_bars,
        min_bars=min(base.min_bars, max(20, len(test_df) // 4)),
        counter_trend_policy=base.counter_trend_policy,
        sr_tol_frac=base.sr_tol_frac,
    )
    oos = run_backtest(pair, timeframe, df=test_df, config=oos_cfg)
    return {
        "pair": pair,
        "timeframe": timeframe,
        "n_bars": len(df),
        "train_bars": len(train_df),
        "test_bars": len(test_df),
        "best_params": best_params,
        "train_best_expectancy_r": round(best_expectancy, 3),
        "oos_summary": oos.summary(),
        "train_grid": train_rows,
    }


def parameter_sweep(
    pair: str,
    timeframe: str,
    df: pd.DataFrame | None = None,
    period: str | None = None,
    atr_sl_grid: Iterable[float] = (1.0, 1.25, 1.5, 2.0, 2.5),
    rr_grid: Iterable[float] = (1.0, 1.5, 2.0, 2.5, 3.0),
    sr_tol_grid: Iterable[float] = (0.0008, 0.0012, 0.0020),
    base_config: BacktestConfig | None = None,
) -> pd.DataFrame:
    """Grid search over SL multiple, R:R, and S/R tolerance."""
    if df is None:
        period = period or ("60d" if timeframe in ("1h", "15m", "4h") else "2y")
        df = _load_candles(pair, timeframe, period)

    base = base_config or BacktestConfig()
    rows = []
    for atr_m in atr_sl_grid:
        for rr in rr_grid:
            for sr in sr_tol_grid:
                cfg = BacktestConfig(
                    threshold=base.threshold,
                    atr_sl_mult=atr_m,
                    rr=rr,
                    max_bars=base.max_bars,
                    min_bars=base.min_bars,
                    counter_trend_policy=base.counter_trend_policy,
                    sr_tol_frac=sr,
                )
                res = run_backtest(pair, timeframe, df=df, config=cfg)
                s = res.summary()
                rows.append(
                    {
                        "pair": pair,
                        "timeframe": timeframe,
                        "atr_sl_mult": atr_m,
                        "rr": rr,
                        "sr_tol_frac": sr,
                        "n_closed": s["n_closed"],
                        "win_rate_pct": s["win_rate_pct"],
                        "expectancy_r": s["expectancy_r"],
                        "total_r": s["total_r"],
                    }
                )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["expectancy_r", "n_closed"], ascending=[False, False]).reset_index(
        drop=True
    )


def monte_carlo(
    result: BacktestResult,
    n_sims: int = 2000,
    risk_per_trade: float = 0.01,
    starting_equity: float = 10_000.0,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Bootstrap the closed-trade R-multiple sequence to estimate equity paths,
    max drawdown, and rough risk-of-ruin under fixed fractional risk.
    """
    df = result.to_frame()
    if df.empty:
        return {"error": "no trades", "pair": result.pair, "timeframe": result.timeframe}
    closed = df[df["outcome"].isin(["win", "loss"])]["pnl_r"].values
    if len(closed) < 2:
        return {"error": "need >=2 closed trades", "n": len(closed)}

    rng = np.random.default_rng(seed)
    n = len(closed)
    terminal = np.zeros(n_sims)
    max_dd = np.zeros(n_sims)
    ruined = 0

    for s in range(n_sims):
        sample = rng.choice(closed, size=n, replace=True)
        equity = starting_equity
        peak = equity
        worst_dd = 0.0
        for r in sample:
            # fractional risk: win adds risk*rr*equity-ish using R units on risk budget
            pnl_cash = equity * risk_per_trade * r
            equity = max(0.0, equity + pnl_cash)
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            worst_dd = max(worst_dd, dd)
            if equity < starting_equity * 0.2:  # 80% drawdown = "ruined" proxy
                ruined += 1
                equity = starting_equity * 0.2
                break
        terminal[s] = equity
        max_dd[s] = worst_dd

    return {
        "pair": result.pair,
        "timeframe": result.timeframe,
        "n_trades": n,
        "n_sims": n_sims,
        "risk_per_trade": risk_per_trade,
        "starting_equity": starting_equity,
        "median_terminal_equity": round(float(np.median(terminal)), 2),
        "p05_terminal_equity": round(float(np.percentile(terminal, 5)), 2),
        "p95_terminal_equity": round(float(np.percentile(terminal, 95)), 2),
        "median_max_drawdown_pct": round(float(np.median(max_dd)) * 100, 1),
        "p95_max_drawdown_pct": round(float(np.percentile(max_dd, 95)) * 100, 1),
        "ruin_rate_pct": round(ruined / n_sims * 100, 2),
        "mean_trade_r": round(float(closed.mean()), 3),
    }


def score_distribution(results: list[BacktestResult]) -> dict[str, float]:
    """Empirical score quantiles across fired trades — for plain-language confidence bands."""
    scores = []
    for r in results:
        for t in r.trades:
            if t.outcome in ("win", "loss", "pending"):
                scores.append(abs(t.score))
    if not scores:
        return {"p33": 50.0, "p66": 70.0, "p50": 55.0, "n": 0}
    arr = np.array(scores)
    return {
        "p33": round(float(np.percentile(arr, 33)), 1),
        "p50": round(float(np.percentile(arr, 50)), 1),
        "p66": round(float(np.percentile(arr, 66)), 1),
        "p90": round(float(np.percentile(arr, 90)), 1),
        "n": len(scores),
    }
