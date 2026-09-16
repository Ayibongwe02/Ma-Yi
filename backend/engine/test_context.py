"""
Stage 3 smoke test — runs context filters on top of Stage 2 pattern
detection and prints a before/after table so you can see how trend, S/R,
and volatility context move (or kill) each raw pattern score.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
sys.path.insert(0, str(Path(__file__).parent))

from candles import fetch_history, _save
from patterns import detect_patterns, PATTERN_COLUMNS
from context import apply_context_filters, compare_before_after

MIN_SCORE = 40

if __name__ == "__main__":
    pair, timeframe = "EURUSD=X", "1h"

    print(f"Loading {pair} @ {timeframe} candles...")
    try:
        df = fetch_history(pair, timeframe, period="10d")
    except Exception as e:
        print(f"Live fetch failed ({e.__class__.__name__}) — using synthetic candles "
              f"(sandbox blocks external finance APIs; works live on your machine).\n")
        from _synthetic import generate_synthetic_candles
        df = generate_synthetic_candles(n=300, freq="1h")
        _save(df, pair, timeframe)

    print(f"Running Stage 2 pattern detection on {len(df)} candles...")
    pattern_df = detect_patterns(df)

    print("Applying Stage 3 context filters (trend / S/R / volatility)...\n")
    context_df = apply_context_filters(df, pattern_df)

    table = compare_before_after(pattern_df, context_df, PATTERN_COLUMNS, min_score=MIN_SCORE)

    if table.empty:
        print(f"No pattern crossed +/-{MIN_SCORE} raw or final on this dataset.")
    else:
        print(f"Before/after for every bar where raw or final score >= {MIN_SCORE}:\n")
        print(table.to_string(index=False))

        print("\nVerdict breakdown:")
        print(table["verdict"].value_counts().to_string())

        confirmed = table[table.verdict == "confirmed"]
        killed = table[table.verdict == "killed"]
        if not confirmed.empty:
            best = confirmed.loc[confirmed.delta.idxmax()]
            print(f"\nStrongest confluence example: {best.pattern} @ {best.timestamp} "
                  f"raw={best.raw_score} -> final={best.final_score} "
                  f"(trend={best.trend}, sr={best.sr_score}, vol={best.volatility})")
        if not killed.empty:
            worst = killed.loc[killed.delta.idxmin()]
            print(f"Noise example killed by context: {worst.pattern} @ {worst.timestamp} "
                  f"raw={worst.raw_score} -> final={worst.final_score} "
                  f"(trend={worst.trend}, sr={worst.sr_score}, vol={worst.volatility})")
