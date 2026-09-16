"""
Quick smoke test for the COT module. Mirrors test_sentiment.py's shape:
tries a live call, falls back to a mocked record set (with no network) if
the CFTC endpoint isn't reachable from this sandbox, so this still
validates the "COT Index" scoring math end to end.
"""

from cot import (
    CotUnavailable,
    cot_index,
    cot_index_to_score,
    fetch_cot_history,
    fetch_cot_score,
)

if __name__ == "__main__":
    currency = "EUR"

    print(f"Fetching CFTC COT history for {currency} ...")
    try:
        records = fetch_cot_history(currency)
        print(f"Got {len(records)} weekly reports, latest: {records[-1]}\n")
        idx = cot_index(records)
        score = cot_index_to_score(idx)
        print(f"COT Index: {idx:.1f}/100  ->  score {score:+.1f}")
    except CotUnavailable as e:
        print(f"Live fetch unavailable ({e})")
        print(
            "Falling back to a mocked report history to validate the "
            "scoring math (this will use the real CFTC feed once network "
            "access to publicreporting.cftc.gov is available).\n"
        )

        # Large specs grinding from net-short to their most net-long reading
        # in the window -> should land near the top of the COT Index.
        mock_records = [
            {"date": f"2026-0{i}-01", "noncommercial_long": 100 + i * 5,
             "noncommercial_short": 150 - i * 5, "open_interest": 500,
             "net": (100 + i * 5) - (150 - i * 5),
             "net_pct_oi": ((100 + i * 5) - (150 - i * 5)) / 500 * 100}
            for i in range(1, 9)
        ]
        idx = cot_index(mock_records)
        score = cot_index_to_score(idx)
        print(f"Mock: net position rising steadily -> COT Index {idx:.1f} -> score {score:+.1f}")
        assert idx > 90, "Latest reading should sit near the top of its own range"
        assert score > 80, "Near-max COT Index should map to a strongly bullish score"

        flat_records = [
            {"date": "2026-01-01", "net_pct_oi": 5.0},
            {"date": "2026-01-08", "net_pct_oi": 5.0},
            {"date": "2026-01-15", "net_pct_oi": 5.0},
        ]
        idx = cot_index(flat_records)
        assert idx == 50.0, "No range in the window should be treated as neutral"
        print(f"Mock: flat history -> COT Index {idx:.1f} (neutral, as expected)")

        one_record = [{"date": "2026-01-01", "net_pct_oi": 12.0}]
        idx = cot_index(one_record)
        assert idx == 50.0, "A single data point (no history yet) should be neutral"
        print(f"Mock: single data point -> COT Index {idx:.1f} (neutral, as expected)")

        print("\nScoring math checks passed.")

    print(f"\nFetching combined COT score for EURUSD=X ...")
    try:
        score = fetch_cot_score("EURUSD=X")
        print(f"EURUSD=X COT score: {score:+.1f}")
    except CotUnavailable as e:
        print(f"Unavailable in this sandbox ({e}) — expected without network access.")
