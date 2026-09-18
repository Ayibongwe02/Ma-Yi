"""
CLI entry point for Stage 4 backtesting across all configured instruments.

Examples:
  python backtest/run_backtest.py
  python backtest/run_backtest.py --pairs EURUSD=X,GBPJPY=X --timeframes 1h
  python backtest/run_backtest.py --sweep --walk-forward --monte-carlo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "backtest"))

from instruments import DEFAULT_TICKERS  # noqa: E402
from backtest.engine import (  # noqa: E402
    BacktestConfig,
    run_backtest,
    per_pattern_breakdown,
    parameter_sweep,
    walk_forward,
    monte_carlo,
    score_distribution,
)

REPORT_DIR = ROOT / "backtest" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _parse_list(raw: str | None, default: list[str]) -> list[str]:
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Ma-yi multi-instrument backtest")
    parser.add_argument(
        "--pairs",
        default=",".join(DEFAULT_TICKERS),
        help="Comma-separated yfinance tickers",
    )
    parser.add_argument("--timeframes", default="1h,4h")
    parser.add_argument("--period-1h", default="60d", help="History depth for 1h (yfinance limit)")
    parser.add_argument(
        "--period-4h",
        default="60d",
        help="History depth for 4h (resampled from 1h; same ~60d yfinance ceiling). "
        "Longer 4h history needs OANDA/Twelve Data, not yfinance.",
    )
    parser.add_argument("--threshold", type=float, default=40.0)
    parser.add_argument("--atr-sl-mult", type=float, default=1.5)
    parser.add_argument("--rr", type=float, default=1.5)
    parser.add_argument("--sweep", action="store_true", help="Run parameter grid search")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward validation")
    parser.add_argument("--monte-carlo", action="store_true", help="Monte Carlo drawdown sims")
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    args = parser.parse_args()

    pairs = _parse_list(args.pairs, DEFAULT_TICKERS)
    timeframes = _parse_list(args.timeframes, ["1h", "4h"])
    base = BacktestConfig(
        threshold=args.threshold,
        atr_sl_mult=args.atr_sl_mult,
        rr=args.rr,
    )

    print("=" * 64)
    print("Ma-yi backtest")
    print(f"  pairs: {pairs}")
    print(f"  timeframes: {timeframes}")
    print(f"  threshold={base.threshold} atr_sl={base.atr_sl_mult} rr={base.rr}")
    print("=" * 64)
    print(
        "NOTE: 4h history beyond ~60 days requires switching the data path from "
        "yfinance to OANDA or Twelve Data (4h is resampled from 1h intraday)."
    )
    print()

    all_results = []
    summary_rows = []
    pattern_frames = []

    for pair in pairs:
        for tf in timeframes:
            period = args.period_1h if tf in ("1h", "15m") else args.period_4h
            print(f"→ {pair} @ {tf} (period={period})")
            res = run_backtest(pair, tf, period=period, config=base)
            s = res.summary()
            all_results.append(res)
            summary_rows.append(s)
            print(
                f"   bars={s['n_bars']} trades={s['n_trades']} closed={s['n_closed']} "
                f"WR={s['win_rate_pct']}% exp={s['expectancy_r']}R total={s['total_r']}R"
            )
            pb = per_pattern_breakdown(res)
            if not pb.empty:
                pattern_frames.append(pb)

            if args.walk_forward:
                wf = walk_forward(pair, tf, period=period, base_config=base)
                print(f"   walk-forward best={wf.get('best_params')} "
                      f"OOS exp={wf.get('oos_summary', {}).get('expectancy_r')}")
                (REPORT_DIR / f"walkforward_{pair.replace('=', '_').replace('^', '')}_{tf}.json").write_text(
                    json.dumps(wf, indent=2, default=str)
                )

            if args.sweep:
                print("   parameter sweep…")
                sweep_df = parameter_sweep(pair, tf, period=period, base_config=base)
                path = REPORT_DIR / f"sweep_{pair.replace('=', '_').replace('^', '')}_{tf}.csv"
                sweep_df.to_csv(path, index=False)
                if not sweep_df.empty:
                    top = sweep_df.iloc[0]
                    print(
                        f"   best sweep: atr={top['atr_sl_mult']} rr={top['rr']} "
                        f"sr={top['sr_tol_frac']} exp={top['expectancy_r']} n={top['n_closed']}"
                    )

            if args.monte_carlo:
                mc = monte_carlo(res, risk_per_trade=args.risk_per_trade)
                print(
                    f"   MC median DD={mc.get('median_max_drawdown_pct')}% "
                    f"p95 DD={mc.get('p95_max_drawdown_pct')}% "
                    f"ruin={mc.get('ruin_rate_pct')}%"
                )
                (REPORT_DIR / f"montecarlo_{pair.replace('=', '_').replace('^', '')}_{tf}.json").write_text(
                    json.dumps(mc, indent=2, default=str)
                )

            # Persist trades
            tdf = res.to_frame()
            if not tdf.empty:
                tdf.to_csv(
                    REPORT_DIR / f"trades_{pair.replace('=', '_').replace('^', '')}_{tf}.csv",
                    index=False,
                )

    print()
    print("--- Comparison ---")
    summary_df = pd.DataFrame(summary_rows)
    # Drop nested config for display
    show_cols = [
        c
        for c in summary_df.columns
        if c != "config"
    ]
    if not summary_df.empty:
        print(summary_df[show_cols].to_string(index=False))
        summary_df.to_csv(REPORT_DIR / "comparison.csv", index=False)

    if pattern_frames:
        all_pat = pd.concat(pattern_frames, ignore_index=True)
        all_pat.to_csv(REPORT_DIR / "per_pattern.csv", index=False)
        print("\n--- Per-pattern (top expectancy) ---")
        print(all_pat.head(20).to_string(index=False))

    dist = score_distribution(all_results)
    (REPORT_DIR / "score_distribution.json").write_text(json.dumps(dist, indent=2))
    print(f"\nScore distribution for confidence labels: {dist}")
    print(f"\nReports written to {REPORT_DIR}")


if __name__ == "__main__":
    main()
