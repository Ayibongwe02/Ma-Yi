"""
Risk controls for Stage 7 execution.

All knobs are env-configurable. Defaults are conservative.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class RiskConfig:
    # Max fraction of equity to risk per trade (e.g. 0.01 = 1%)
    risk_per_trade: float = 0.01
    # Hard cap on absolute units (OANDA FX units; 1000 ≈ 0.01 lot)
    max_units: int = 1000
    # Minimum |final_score| required to execute
    min_score: float = 50.0
    # Max concurrent open positions
    max_open_positions: int = 2
    # Max new trades in a UTC calendar day
    max_daily_trades: int = 5
    # Kill switch — when true, no new orders
    kill_switch: bool = False
    # Default account equity used for sizing in dry-run / when balance unknown
    default_equity: float = 10_000.0

    @classmethod
    def from_env(cls) -> "RiskConfig":
        def f(key: str, default: float) -> float:
            try:
                return float(os.environ.get(key, default))
            except (TypeError, ValueError):
                return default

        def i(key: str, default: int) -> int:
            try:
                return int(float(os.environ.get(key, default)))
            except (TypeError, ValueError):
                return default

        kill = os.environ.get("EXEC_KILL_SWITCH", "false").strip().lower() in (
            "1", "true", "yes", "on",
        )
        return cls(
            risk_per_trade=f("RISK_PER_TRADE", 0.01),
            max_units=i("MAX_UNITS", 1000),
            min_score=f("EXEC_MIN_SCORE", 50.0),
            max_open_positions=i("MAX_OPEN_POSITIONS", 2),
            max_daily_trades=i("MAX_DAILY_TRADES", 5),
            kill_switch=kill,
            default_equity=f("DEFAULT_EQUITY", 10_000.0),
        )


class RiskManager:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig.from_env()
        self._daily_count = 0
        self._daily_date = datetime.now(timezone.utc).date()

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_count = 0

    def record_trade(self) -> None:
        self._roll_day()
        self._daily_count += 1

    def check(
        self,
        *,
        score: float,
        pair: str,
        open_positions: list[dict[str, Any]],
        equity: float | None = None,
    ) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        self._roll_day()
        cfg = self.config

        if cfg.kill_switch:
            return False, "kill switch is ON (EXEC_KILL_SWITCH)"

        if abs(score) < cfg.min_score:
            return False, f"score {score:.1f} below EXEC_MIN_SCORE={cfg.min_score}"

        if len(open_positions) >= cfg.max_open_positions:
            return False, f"open positions {len(open_positions)} >= MAX_OPEN_POSITIONS={cfg.max_open_positions}"

        # Avoid stacking same instrument
        inst = pair.upper().replace("=X", "").replace("_", "").replace("/", "")
        for p in open_positions:
            p_inst = str(p.get("instrument", "")).upper().replace("_", "").replace("/", "")
            if p_inst == inst or inst in p_inst or p_inst in inst:
                return False, f"already have open position in {pair}"

        if self._daily_count >= cfg.max_daily_trades:
            return False, f"daily trade limit reached ({cfg.max_daily_trades})"

        return True, "ok"

    def position_size(
        self,
        *,
        entry: float,
        sl: float,
        equity: float | None = None,
        pair: str = "EURUSD",
    ) -> int:
        """
        Units such that (entry-sl) * units ≈ risk_per_trade * equity.

        For FX pairs quoted to 0.0001, 1 unit ≈ $0.0001 move per unit on XXX/USD-style.
        OANDA 'units' are the base-currency amount (e.g. 1000 units EUR_USD = 0.01 lot).
        Simplified: risk_amount / stop_distance, capped by max_units.
        """
        cfg = self.config
        equity = equity if equity and equity > 0 else cfg.default_equity
        stop = abs(entry - sl)
        if stop <= 0:
            return 0

        risk_amount = equity * cfg.risk_per_trade
        # Approximate: for EUR_USD, P/L per unit per price unit ≈ 1 (quote currency)
        raw_units = risk_amount / stop
        units = int(max(1, min(cfg.max_units, raw_units)))
        return units
