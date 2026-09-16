"""
Stage 2 smoke test — runs pattern detection against candle data from
Stage 1 (real feed if available, synthetic fallback otherwise, matching
data/test_fetch.py's behavior) and prints detected signals above a
threshold score.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
sys.path.insert(0, str(Path(__file__).parent))

from candles import fetch_history, _save
from patterns import detect_patterns, summarize_signals

if __name__ == "__main__":
    pair, timeframe = "EURUSD=X", "1h"

    print(f"Loading {pair} @ {timeframe} candles...")
    try:
        df = fetch_history(pair, timeframe, period="10d")
    except Exception as e:
        print(f"Live fetch failed ({e.__class__.__name__}) — using synthetic candles "
              f"(sandbox blocks external finance APIs; works live on your machine).\n")
        sys.path.insert(0, str(Path(__file__).parent.parent / "data"))
        from _synthetic import generate_synthetic_candles
        df = generate_synthetic_candles(n=300, freq="1h")
        _save(df, pair, timeframe)

    print(f"Running pattern detection on {len(df)} candles...\n")
    pattern_df = detect_patterns(df)

    signals = summarize_signals(df, pattern_df, min_score=40)
    print(f"Detected {len(signals)} pattern signals with score >= 40:\n")
    print(signals.to_string(index=False))

    print("\nBreakdown by pattern type:")
    print(signals.groupby(["pattern", "direction"]).size().to_string())
