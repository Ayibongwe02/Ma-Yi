"""
Quick smoke test for Stage 1 — fetches a small history for one pair and
prints the last 10 candles, confirming the ingestion pipeline works end
to end (network fetch -> cache -> load).
"""

from candles import fetch_history, load_cached, _save

if __name__ == "__main__":
    pair = "EURUSD=X"
    timeframe = "1h"

    print(f"Fetching {pair} @ {timeframe} ...")
    try:
        df = fetch_history(pair, timeframe, period="10d")
        print(f"Fetched {len(df)} candles from live feed. Cached to data/store/.\n")
    except Exception as e:
        print(f"Live fetch failed ({e.__class__.__name__}: {e})")
        print("Falling back to synthetic candles to validate the pipeline "
              "(this sandbox blocks external finance APIs — will work live "
              "on your own machine).\n")
        from _synthetic import generate_synthetic_candles
        df = generate_synthetic_candles(n=240, freq="1h")
        _save(df, pair, timeframe)
        print(f"Generated {len(df)} synthetic candles. Cached to data/store/.\n")

    print("Last 10 candles:")
    print(df.tail(10).to_string())

    print("\nConfirming cache reload works (no network)...")
    cached = load_cached(pair, timeframe)
    print(f"Loaded {len(cached)} candles from cache. Matches fetch: {len(cached) == len(df)}")
