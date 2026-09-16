"""
Stage 5 — Live / replay signal delivery (+ optional Stage 7 execution).

On each new closed candle (or while replaying history for testing):
  1. Run pattern detection + context filters
  2. [live mode only] Fetch live retail sentiment (IG) and institutional
     COT positioning (CFTC), and blend both into the final bar's score,
     including a retail-vs-institutional divergence check (see
     engine/confidence.py's combine_signals()) — neither has real history
     wired in here, so they never touch replayed/historical bars.
  3. For any pattern whose |final_score| >= threshold, compute trade levels
  4. Log every candidate (fired or not)
  5. If fired, send an alert (Telegram or console fallback)
  6. Optionally route through Stage 7 executor (dry-run by default)

Real broker orders only when EXECUTE_ENABLED=true and OANDA credentials are set.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "delivery"))

import os

from candles import fetch_history, load_pairs_from_env
from instruments import scale_params
from patterns import detect_patterns, PATTERN_COLUMNS
from context import apply_context_filters
from confidence import combine_signals
from trade_params import compute_trade_levels, simulate_outcome
from logger import init_db, log_signal
from alerts import send_signal_alert

try:
    from sentiment import fetch_sentiment_score, sentiment_to_score, SentimentUnavailable
except ImportError:  # requests not installed, etc — sentiment stays optional
    fetch_sentiment_score = None
    SentimentUnavailable = Exception

try:
    from cot import fetch_cot_score, CotUnavailable
except ImportError:  # requests not installed, etc — COT stays optional
    fetch_cot_score = None
    CotUnavailable = Exception


def _live_sentiment_score(pair: str) -> float | None:
    """Fetch a live contrarian retail sentiment score for `pair`, or None if
    unavailable (no credentials, API error, unmapped pair). Never raises —
    sentiment is a nice-to-have overlay, not a dependency for signals."""
    if fetch_sentiment_score is None:
        return None
    try:
        return fetch_sentiment_score(pair)
    except SentimentUnavailable as e:
        print(f"[sentiment] unavailable for {pair} ({e}) — using pattern/context score only")
        return None


def _live_cot_score(pair: str) -> float | None:
    """Fetch the latest institutional COT (Commitment of Traders) score for
    `pair`, or None if unavailable (unmapped instrument, network error,
    stale/empty CFTC response). Never raises — COT is a nice-to-have
    overlay, not a dependency for signals. See data/cot.py."""
    if fetch_cot_score is None:
        return None
    try:
        return fetch_cot_score(pair)
    except CotUnavailable as e:
        print(f"[cot] unavailable for {pair} ({e}) — using pattern/context(+sentiment) score only")
        return None

DEFAULT_THRESHOLD = 40.0

# "ignore_short" (default): only trade with the long-term trend, treat any
# short-term counter-move as noise. "fade_early": also allow young, strong
# counter-trend moves through at a discount. See engine/context.py.
COUNTER_TREND_POLICY = os.environ.get("COUNTER_TREND_POLICY", "ignore_short")


def _best_pattern(row: pd.Series, pattern_cols: list[str]) -> tuple[str, float]:
    """Pick the pattern with the largest absolute final score on this bar."""
    best_name, best_score = "", 0.0
    for col in pattern_cols:
        val = float(row.get(col, 0) or 0)
        if abs(val) > abs(best_score):
            best_name, best_score = col, val
    return best_name, best_score


def process_dataframe(
    df: pd.DataFrame,
    pair: str,
    timeframe: str,
    threshold: float = DEFAULT_THRESHOLD,
    alert: bool = True,
    simulate: bool = True,
    min_bars: int = 60,
    executor=None,
    execute_only_last: bool = False,
    sentiment_score: float | None = None,
    cot_score: float | None = None,
) -> list[dict]:
    """
    Run the full engine on a candle DataFrame and log/alert signals.
    If `executor` is provided, fired signals are also sent to Stage 7.
    When execute_only_last=True, only the final bar is submitted for execution
    (used in live mode so historical replay doesn't spam orders).

    sentiment_score: a live contrarian retail sentiment score (-100..+100,
    from data/sentiment.py), or None.
    cot_score: a live institutional COT positioning score (-100..+100, from
    data/cot.py), or None.
    Both, if given, are blended into the score of ONLY the final bar — they
    reflect positioning *right now*, so it would be wrong to apply them
    retroactively to earlier bars in a replay. When both are present, they
    also get compared for retail-vs-institutional divergence (see
    engine/confidence.py's combine_signals()) — agreement between a
    crowded retail read and large-speculator positioning is a stronger
    signal than either alone; disagreement dampens it.
    """
    if len(df) < min_bars:
        print(f"[{pair} {timeframe}] only {len(df)} bars — need >= {min_bars}, skip")
        return []

    init_db()
    scale = scale_params(pair)
    pattern_df = detect_patterns(df, tweezer_tol=scale["tweezer_tol_rel"])
    context_df = apply_context_filters(
        df,
        pattern_df,
        counter_trend_policy=COUNTER_TREND_POLICY,
        round_increment=scale["round_increment"],
    )

    fired_signals: list[dict] = []
    last_i = len(df) - 1

    for i in range(min_bars, len(df)):
        row = context_df.iloc[i]
        pattern, score = _best_pattern(row, PATTERN_COLUMNS)
        if pattern == "" or abs(score) < 1e-6:
            continue

        if i == last_i and (sentiment_score is not None or cot_score is not None):
            score = combine_signals(score, sentiment_score, cot_score)

        direction = 1 if score > 0 else -1
        fired = abs(score) >= threshold

        trend = int(row.get(f"{pattern}_trend", 0) or 0)
        short_trend = int(row.get(f"{pattern}_short_trend", 0) or 0)
        regime = str(row.get(f"{pattern}_regime", "") or "")
        structure = str(row.get(f"{pattern}_structure", "") or "")
        zone = str(row.get(f"{pattern}_zone", "") or "")
        sr = float(row.get(f"{pattern}_sr", 0) or 0)
        vol = str(row.get(f"{pattern}_vol", "normal") or "normal")
        raw = float(pattern_df[pattern].iloc[i]) if pattern in pattern_df.columns else None

        levels = None
        outcome = "pending"
        if fired:
            levels = compute_trade_levels(
                df, i, direction, price_decimals=scale["price_decimals"]
            )
            if simulate:
                outcome = simulate_outcome(
                    df, i, direction, levels["sl"], levels["tp"]
                )

        bar_time = df.index[i]
        bar_ts = bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time)

        alerted = False
        if fired and alert:
            alerted = send_signal_alert(
                pair=pair,
                timeframe=timeframe,
                pattern=pattern,
                direction=direction,
                score=score,
                entry=levels["entry"],
                sl=levels["sl"],
                tp=levels["tp"],
                bar_time=bar_ts,
                regime=regime,
                sr_score=sr,
                volatility=vol,
            )

        sid = log_signal(
            pair=pair,
            timeframe=timeframe,
            bar_time=bar_ts,
            pattern=pattern,
            direction=direction,
            final_score=score,
            fired=fired,
            raw_score=raw,
            entry=levels["entry"] if levels else None,
            sl=levels["sl"] if levels else None,
            tp=levels["tp"] if levels else None,
            risk=levels["risk"] if levels else None,
            rr=levels["rr"] if levels else None,
            trend=trend,
            sr_score=sr,
            volatility=vol,
            outcome=outcome if fired else "n/a",
            alerted=alerted,
            meta={
                "short_trend": short_trend, "regime": regime,
                "counter_trend_policy": COUNTER_TREND_POLICY,
                "structure": structure, "zone": zone,
            },
        )

        exec_result = None
        if fired and executor is not None:
            should_exec = (not execute_only_last) or (i == last_i)
            if should_exec:
                exec_result = executor.execute_signal(
                    pair=pair,
                    direction=direction,
                    score=score,
                    entry=levels["entry"],
                    sl=levels["sl"],
                    tp=levels["tp"],
                    signal_id=sid,
                    pattern=pattern,
                )

        if fired:
            rec = {
                "id": sid,
                "pair": pair,
                "timeframe": timeframe,
                "bar_time": bar_ts,
                "pattern": pattern,
                "direction": direction,
                "score": score,
                "entry": levels["entry"],
                "sl": levels["sl"],
                "tp": levels["tp"],
                "outcome": outcome,
                "alerted": alerted,
                "execution": None if exec_result is None else {
                    "ok": exec_result.ok,
                    "dry_run": exec_result.dry_run,
                    "order_id": exec_result.order_id,
                    "message": exec_result.message,
                },
            }
            fired_signals.append(rec)
            exec_note = ""
            if exec_result is not None:
                exec_note = f" | exec={'OK' if exec_result.ok else 'BLOCKED'} dry={exec_result.dry_run}"
            print(
                f"  FIRED  {pair} {timeframe} {pattern} score={score:.1f} "
                f"{'LONG' if direction > 0 else 'SHORT'} "
                f"entry={levels['entry']} sl={levels['sl']} tp={levels['tp']} "
                f"→ {outcome}{exec_note}"
            )

    return fired_signals


def replay_recent(
    pair: str = "EURUSD=X",
    timeframe: str = "1h",
    period: str = "10d",
    threshold: float = DEFAULT_THRESHOLD,
    alert: bool = False,
    executor=None,
) -> list[dict]:
    """Replay last N days of data (or synthetic fallback) and emit signals."""
    print(f"Replaying {pair} @ {timeframe} (period={period})...")
    try:
        df = fetch_history(pair, timeframe, period=period)
    except Exception as e:
        print(f"  Live fetch failed ({e.__class__.__name__}) — synthetic fallback")
        from _synthetic import generate_synthetic_candles
        from candles import _save

        df = generate_synthetic_candles(n=300, freq="1h")
        _save(df, pair, timeframe)

    print(f"  {len(df)} candles loaded. Running engine...")
    signals = process_dataframe(
        df,
        pair,
        timeframe,
        threshold=threshold,
        alert=alert,
        simulate=True,
        executor=executor,
        execute_only_last=False if executor is None else True,
        # For replay+execute: only execute the *last* fired bar to avoid
        # flooding; set execute_only_last and still process history for logs.
    )
    # When executor is present during replay we already limited execution
    # to the last bar via execute_only_last=True above.
    print(f"  Done. {len(signals)} signals fired (threshold={threshold}).")
    return signals


def live_once(
    pair: str,
    timeframe: str,
    lookback_bars: int = 120,
    threshold: float = DEFAULT_THRESHOLD,
    executor=None,
) -> list[dict]:
    """
    Pull recent history, process window for indicators, execute/alert only
    on the newest closed bar.
    """
    try:
        df = fetch_history(pair, timeframe, period="10d")
    except Exception as e:
        print(f"[{pair}] fetch failed: {e}")
        return []

    if len(df) < lookback_bars:
        lookback_bars = len(df)

    sentiment_score = _live_sentiment_score(pair)
    cot_score = _live_cot_score(pair)

    signals = process_dataframe(
        df.iloc[-lookback_bars:],
        pair,
        timeframe,
        threshold=threshold,
        alert=True,
        simulate=False,
        executor=executor,
        execute_only_last=True,
        sentiment_score=sentiment_score,
        cot_score=cot_score,
    )
    if signals:
        last_ts = str(df.index[-1])
        signals = [s for s in signals if s["bar_time"].startswith(last_ts[:16])]
    return signals


def main():
    parser = argparse.ArgumentParser(description="Forex signal delivery runner")
    parser.add_argument(
        "--mode",
        choices=["replay", "live"],
        default="replay",
        help="replay = historical test; live = fetch latest and alert",
    )
    parser.add_argument("--pair", default=None, help="Single pair; omit with --all-pairs for env PAIRS")
    parser.add_argument("--all-pairs", action="store_true", help="Replay/live across PAIRS from env")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--period", default="10d")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument(
        "--alert",
        action="store_true",
        help="Send Telegram (or console) alerts during replay",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Route fired signals through Stage 7 executor (dry-run unless EXECUTE_ENABLED=true)",
    )
    parser.add_argument(
        "--live-broker",
        action="store_true",
        help="Allow real OANDA orders (still requires EXECUTE_ENABLED=true). Without this, force dry-run.",
    )
    args = parser.parse_args()

    executor = None
    if args.execute:
        from execution.executor import Executor
        # Safety: force dry-run unless user explicitly passes --live-broker
        force_dry = not args.live_broker
        executor = Executor(force_dry_run=force_dry)
        mode = "DRY-RUN" if force_dry or executor.force_dry_run else "LIVE BROKER"
        print(f"[execution] Stage 7 enabled — mode={mode}")

    pairs_env, timeframes_env = load_pairs_from_env()
    if args.timeframe:
        timeframes_env = [args.timeframe]

    if args.mode == "replay":
        if args.all_pairs or not args.pair:
            pairs = pairs_env if (args.all_pairs or not args.pair) else [args.pair]
            if args.pair and not args.all_pairs:
                pairs = [args.pair]
            # Default scan from API: all env pairs when --pair omitted
            if not args.pair and not args.all_pairs:
                pairs = pairs_env
            all_fired = []
            for p in pairs:
                print(f"[replay] {p} {args.timeframe} period={args.period}")
                sigs = replay_recent(
                    pair=p,
                    timeframe=args.timeframe,
                    period=args.period,
                    threshold=args.threshold,
                    alert=args.alert,
                    executor=executor,
                )
                all_fired.extend(sigs)
            print(f"\nSummary: {len(all_fired)} fired signals logged to delivery/logs/signals.jsonl")
        else:
            signals = replay_recent(
                pair=args.pair,
                timeframe=args.timeframe,
                period=args.period,
                threshold=args.threshold,
                alert=args.alert,
                executor=executor,
            )
            print(f"\nSummary: {len(signals)} fired signals logged to delivery/logs/signals.jsonl")
    else:
        pairs = pairs_env
        if args.pair and not args.all_pairs:
            pairs = [args.pair]
        timeframes = timeframes_env
        all_sigs = []
        for p in pairs:
            for tf in timeframes:
                all_sigs.extend(
                    live_once(p, tf, threshold=args.threshold, executor=executor)
                )
        print(f"Live pass complete — {len(all_sigs)} new alerts.")

    # Auto-retrain when replay/live produced new win/loss labels on fired signals
    try:
        from engine.ml.learner import auto_retrain
        result = auto_retrain(reason=f"runner:{args.mode}")
        if result.get("ok"):
            print(
                f"[ml] auto-retrain ok n={result.get('n_samples')} "
                f"acc={result.get('train_accuracy')}"
            )
        elif result.get("skipped"):
            print(f"[ml] auto-retrain skipped: {result.get('reason')}")
    except Exception as e:
        print(f"[ml] auto-retrain unavailable: {e}")


if __name__ == "__main__":
    main()
