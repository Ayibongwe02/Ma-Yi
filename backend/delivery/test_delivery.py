"""
Smoke test for Stage 5: run the engine against synthetic/recent data,
confirm signals are logged, and print a sample of what would have been alerted.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "delivery"))

from runner import replay_recent
from logger import get_signals, stats, init_db


def main():
    init_db()
    print("=== Stage 5 delivery smoke test ===\n")
    signals = replay_recent(
        pair="EURUSD=X",
        timeframe="1h",
        period="10d",
        threshold=40.0,
        alert=True,  # will fall back to console without Telegram creds
    )

    print("\n--- Fired signals (sample) ---")
    for s in signals[:8]:
        side = "LONG" if s["direction"] > 0 else "SHORT"
        print(
            f"  {s['bar_time'][:19]}  {s['pattern']:22} {side:5} "
            f"score={s['score']:6.1f}  entry={s['entry']}  "
            f"SL={s['sl']}  TP={s['tp']}  → {s['outcome']}"
        )
    if len(signals) > 8:
        print(f"  ... and {len(signals) - 8} more")

    st = stats()
    print("\n--- Aggregate (fired only) ---")
    print(
        f"  total_fired={st['total_fired']}  wins={st['wins']}  "
        f"losses={st['losses']}  pending={st['pending']}  "
        f"win_rate={st['win_rate']}%"
    )

    rows = get_signals(fired_only=True, limit=5)
    print(f"\nLogger OK — {len(rows)} recent fired rows readable from JSONL log.")
    print("Stage 5 smoke test complete.")


if __name__ == "__main__":
    main()
