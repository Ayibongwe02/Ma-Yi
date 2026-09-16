"""
Stage 7 smoke test — always dry-run.

Verifies risk gates, position sizing, and that the executor records orders
without touching a real broker.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution.broker import DryRunBroker, OrderRequest, to_oanda_instrument
from execution.risk import RiskConfig, RiskManager
from execution.executor import Executor


def test_instrument_mapping():
    assert to_oanda_instrument("EURUSD=X") == "EUR_USD"
    assert to_oanda_instrument("GBPUSD") == "GBP_USD"
    assert to_oanda_instrument("EUR_USD") == "EUR_USD"
    assert to_oanda_instrument("^DJI") == "US30_USD"
    assert to_oanda_instrument("US30") == "US30_USD"
    assert to_oanda_instrument("GBPJPY=X") == "GBP_JPY"
    assert to_oanda_instrument("USDJPY=X") == "USD_JPY"
    print("  instrument mapping OK")


def test_position_sizing():
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_units=5000, default_equity=10_000))
    # 1% of 10k = $100 risk; stop 0.0010 → 100_000 units raw, capped at 5000
    units = rm.position_size(entry=1.1000, sl=1.0990, equity=10_000)
    assert 1 <= units <= 5000
    print(f"  position size OK → {units} units")


def test_risk_blocks_low_score():
    rm = RiskManager(RiskConfig(min_score=50))
    ok, reason = rm.check(score=35, pair="EURUSD=X", open_positions=[])
    assert not ok and "score" in reason.lower()
    print(f"  low-score block OK → {reason}")


def test_risk_blocks_kill_switch():
    rm = RiskManager(RiskConfig(kill_switch=True))
    ok, reason = rm.check(score=90, pair="EURUSD=X", open_positions=[])
    assert not ok and "kill" in reason.lower()
    print(f"  kill switch OK → {reason}")


def test_dry_run_order():
    broker = DryRunBroker()
    ex = Executor(broker=broker, risk=RiskManager(RiskConfig(min_score=40, max_units=1000)))
    result = ex.execute_signal(
        pair="EURUSD=X",
        direction=1,
        score=72.5,
        entry=1.08500,
        sl=1.08350,
        tp=1.08725,
        signal_id=999,
        pattern="engulfing",
    )
    assert result.ok and result.dry_run
    assert result.order_id and result.order_id.startswith("DRY-")
    assert len(broker.orders) == 1
    assert len(broker.open_positions()) == 1
    print(f"  dry-run order OK → {result.message}")


def test_duplicate_position_blocked():
    broker = DryRunBroker()
    cfg = RiskConfig(min_score=40, max_open_positions=2)
    ex = Executor(broker=broker, risk=RiskManager(cfg))
    ex.execute_signal(
        pair="EURUSD=X", direction=1, score=80, entry=1.08, sl=1.079, tp=1.082, signal_id=1
    )
    result = ex.execute_signal(
        pair="EURUSD=X", direction=-1, score=80, entry=1.08, sl=1.081, tp=1.078, signal_id=2
    )
    assert not result.ok and "already" in result.message.lower()
    print(f"  duplicate block OK → {result.message}")


def main():
    print("=== Stage 7 execution smoke test (dry-run only) ===\n")
    test_instrument_mapping()
    test_position_sizing()
    test_risk_blocks_low_score()
    test_risk_blocks_kill_switch()
    test_dry_run_order()
    test_duplicate_position_blocked()
    print("\nAll Stage 7 checks passed.")


if __name__ == "__main__":
    main()
