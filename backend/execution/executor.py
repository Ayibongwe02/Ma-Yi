"""
Signal → risk check → broker order.

Used by the delivery runner when --execute is passed (or EXECUTE_ENABLED=true).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from execution.broker import Broker, DryRunBroker, OrderRequest, OrderResult, get_broker
from execution.risk import RiskConfig, RiskManager

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
EXEC_LOG = LOG_DIR / "orders.jsonl"


def _append_exec_log(row: dict[str, Any]) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(EXEC_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except OSError:
        # Fallback for restricted FS
        fallback = Path("/tmp/forex_signal_logs/orders.jsonl")
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


class Executor:
    def __init__(
        self,
        broker: Broker | None = None,
        risk: RiskManager | None = None,
        force_dry_run: bool = False,
    ) -> None:
        self.broker = broker or get_broker(force_dry_run=force_dry_run)
        self.risk = risk or RiskManager()
        self.force_dry_run = force_dry_run or isinstance(self.broker, DryRunBroker)

    def execute_signal(
        self,
        *,
        pair: str,
        direction: int,
        score: float,
        entry: float,
        sl: float,
        tp: float,
        signal_id: int | None = None,
        pattern: str | None = None,
    ) -> OrderResult:
        # Account context
        try:
            summary = self.broker.account_summary()
        except Exception as e:
            summary = {"mode": "unknown", "error": str(e)}
        equity = summary.get("balance") or summary.get("nav") or None
        try:
            open_pos = self.broker.open_positions()
        except Exception:
            open_pos = []

        allowed, reason = self.risk.check(
            score=score,
            pair=pair,
            open_positions=open_pos,
            equity=equity,
        )
        if not allowed:
            result = OrderResult(
                ok=False,
                order_id=None,
                fill_price=None,
                message=f"Blocked by risk: {reason}",
                dry_run=self.force_dry_run,
            )
            _append_exec_log(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "blocked",
                    "pair": pair,
                    "direction": direction,
                    "score": score,
                    "reason": reason,
                    "signal_id": signal_id,
                }
            )
            print(f"[execution] {result.message}")
            return result

        units = self.risk.position_size(entry=entry, sl=sl, equity=equity, pair=pair)
        if units <= 0:
            result = OrderResult(
                False, None, None, "Position size computed as 0", dry_run=self.force_dry_run
            )
            print(f"[execution] {result.message}")
            return result

        req = OrderRequest(
            pair=pair,
            direction=direction,
            units=units,
            entry=entry,
            sl=sl,
            tp=tp,
            signal_id=signal_id,
            pattern=pattern,
            score=score,
        )
        result = self.broker.place_market_order(req)
        if result.ok:
            self.risk.record_trade()

        _append_exec_log(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "order",
                "ok": result.ok,
                "dry_run": result.dry_run,
                "pair": pair,
                "direction": direction,
                "units": units,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "score": score,
                "pattern": pattern,
                "signal_id": signal_id,
                "order_id": result.order_id,
                "fill_price": result.fill_price,
                "message": result.message,
            }
        )
        return result
