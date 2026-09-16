"""
Quick smoke test for the IG sentiment module. Mirrors test_fetch.py's shape:
tries a live call, falls back gracefully (with a fake in-memory response)
if IG credentials aren't set or the sandbox blocks outbound calls, so this
still validates the scoring math end to end.
"""

from sentiment import (
    fetch_sentiment,
    fetch_sentiment_score,
    sentiment_to_score,
    SentimentUnavailable,
)

if __name__ == "__main__":
    pair = "EURUSD=X"

    print(f"Fetching IG sentiment for {pair} ...")
    try:
        data = fetch_sentiment(pair)
        print(f"Live sentiment: {data}\n")
        score = sentiment_to_score(data["long_pct"])
        print(f"Contrarian score: {score:+.1f}")
    except SentimentUnavailable as e:
        print(f"Live fetch unavailable ({e})")
        print("Falling back to a mocked response to validate the scoring "
              "math (this will use real IG data once IG_API_KEY / "
              "IG_USERNAME / IG_PASSWORD are set in .env).\n")

        fake_long_pct = 72.0
        score = sentiment_to_score(fake_long_pct)
        print(f"Mock: {fake_long_pct}% long -> contrarian score {score:+.1f}")
        assert score < 0, "Heavily long crowd should produce a bearish (negative) score"

        fake_long_pct = 28.0
        score = sentiment_to_score(fake_long_pct)
        print(f"Mock: {fake_long_pct}% long -> contrarian score {score:+.1f}")
        assert score > 0, "Heavily short crowd should produce a bullish (positive) score"

        fake_long_pct = 50.0
        score = sentiment_to_score(fake_long_pct)
        print(f"Mock: {fake_long_pct}% long -> contrarian score {score:+.1f}")
        assert score == 0, "50/50 split should be neutral"

        print("\nScoring math checks passed.")
