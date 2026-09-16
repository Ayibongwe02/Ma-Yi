"""
Broker adapters for Stage 7.

- DryRunBroker: logs intended orders, never hits a network (default / safe).
- OandaBroker: OANDA v20 REST market orders with SL + TP.

Enable real execution only with EXECUTE_ENABLED=true and valid credentials.
"""

from __future__ import annotations

import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class OrderRequest:
    pair: str              # yfinance-style e.g. EURUSD=X or OANDA EUR_USD
    direction: int         # +1 long, -1 short
    units: int             # absolute size; sign applied from direction
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    signal_id: int | None = None
    pattern: str | None = None
    score: float | None = None
    client_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])


@dataclass
class OrderResult:
    ok: bool
    order_id: str | None
    fill_price: float | None
    message: str
    raw: dict | None = None
    dry_run: bool = False


def to_oanda_instrument(pair: str) -> str:
    """Resolve ticker / alias to OANDA instrument via the registry.

    Handles FX (EURUSD=X → EUR_USD) and indices (^DJI → US30_USD). Falls
    back to the old 6-letter FX heuristic only for unregistered keys.
    """
    try:
        import sys
        from pathlib import Path as _Path
        root = _Path(__file__).resolve().parent.parent
        data_path = str(root / "data")
        if data_path not in sys.path:
            sys.path.insert(0, data_path)
        from instruments import to_oanda_instrument as _registry_oanda
        return _registry_oanda(pair)
    except Exception:
        pass
    p = pair.upper().replace("=X", "").replace("=x", "").replace("/", "").replace("_", "")
    if len(p) == 6 and p.isalpha():
        return f"{p[:3]}_{p[3:]}"
    return pair


class Broker(ABC):
    @abstractmethod
    def place_market_order(self, req: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def close_position(self, pair: str) -> OrderResult:
        ...

    @abstractmethod
    def open_positions(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def account_summary(self) -> dict[str, Any]:
        ...


class DryRunBroker(Broker):
    """Paper broker — records orders in memory, never sends them."""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self._positions: dict[str, dict[str, Any]] = {}

    def place_market_order(self, req: OrderRequest) -> OrderResult:
        instrument = to_oanda_instrument(req.pair)
        units = abs(int(req.units)) * (1 if req.direction > 0 else -1)
        order_id = f"DRY-{req.client_id}"
        rec = {
            "order_id": order_id,
            "instrument": instrument,
            "units": units,
            "sl": req.sl,
            "tp": req.tp,
            "signal_id": req.signal_id,
            "pattern": req.pattern,
            "score": req.score,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self.orders.append(rec)
        self._positions[instrument] = {
            "instrument": instrument,
            "units": units,
            "avgPrice": req.entry,
            "stopLoss": req.sl,
            "takeProfit": req.tp,
        }
        msg = (
            f"[DRY-RUN] {'BUY' if units > 0 else 'SELL'} {abs(units)} {instrument} "
            f"SL={req.sl} TP={req.tp} (signal_id={req.signal_id})"
        )
        print(msg)
        return OrderResult(
            ok=True,
            order_id=order_id,
            fill_price=req.entry,
            message=msg,
            raw=rec,
            dry_run=True,
        )

    def close_position(self, pair: str) -> OrderResult:
        instrument = to_oanda_instrument(pair)
        pos = self._positions.pop(instrument, None)
        if not pos:
            return OrderResult(False, None, None, f"No open position for {instrument}", dry_run=True)
        msg = f"[DRY-RUN] Closed {instrument} units={pos['units']}"
        print(msg)
        return OrderResult(True, f"CLOSE-{instrument}", pos.get("avgPrice"), msg, raw=pos, dry_run=True)

    def open_positions(self) -> list[dict[str, Any]]:
        return list(self._positions.values())

    def account_summary(self) -> dict[str, Any]:
        return {
            "mode": "dry_run",
            "open_positions": len(self._positions),
            "orders_placed": len(self.orders),
            "balance": None,
        }


class OandaBroker(Broker):
    """
    OANDA v20 REST client.

    Practice (demo): https://api-fxpractice.oanda.com
    Live:            https://api-fxtrade.oanda.com

    Requires OANDA_API_KEY + OANDA_ACCOUNT_ID. Set OANDA_ENV=practice|live.
    """

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        env: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OANDA_API_KEY", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")
        env = (env or os.environ.get("OANDA_ENV", "practice")).lower()
        if env not in ("practice", "live"):
            raise ValueError("OANDA_ENV must be 'practice' or 'live'")
        self.env = env
        host = (
            "https://api-fxpractice.oanda.com"
            if env == "practice"
            else "https://api-fxtrade.oanda.com"
        )
        self.base = f"{host}/v3"
        if not self.api_key or not self.account_id:
            raise ValueError(
                "OANDA_API_KEY and OANDA_ACCOUNT_ID are required for live/practice execution"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        resp = requests.request(
            method, url, headers=self._headers(), json=json_body, timeout=20
        )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            raise RuntimeError(
                f"OANDA {method} {path} → {resp.status_code}: {data}"
            )
        return data

    def place_market_order(self, req: OrderRequest) -> OrderResult:
        instrument = to_oanda_instrument(req.pair)
        units = abs(int(req.units)) * (1 if req.direction > 0 else -1)

        order: dict[str, Any] = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "clientExtensions": {
                "id": req.client_id,
                "tag": req.pattern or "signal",
                "comment": f"score={req.score} sid={req.signal_id}",
            },
        }
        # Format prices without forcing 5dp — indices need fewer decimals,
        # JPY pairs typically 3. Strip trailing zeros for OANDA compatibility.
        def _px(x: float) -> str:
            s = f"{x:.5f}".rstrip("0").rstrip(".")
            return s if s else "0"

        if req.sl is not None:
            order["stopLossOnFill"] = {"price": _px(req.sl)}
        if req.tp is not None:
            order["takeProfitOnFill"] = {"price": _px(req.tp)}

        try:
            data = self._request(
                "POST",
                f"/accounts/{self.account_id}/orders",
                {"order": order},
            )
        except Exception as e:
            return OrderResult(False, None, None, str(e), dry_run=False)

        fill = data.get("orderFillTransaction") or data.get("orderCreateTransaction") or {}
        order_id = str(fill.get("id") or data.get("lastTransactionID") or "")
        fill_price = None
        if "price" in fill:
            try:
                fill_price = float(fill["price"])
            except (TypeError, ValueError):
                pass

        ok = "orderFillTransaction" in data or "orderCreateTransaction" in data
        msg = (
            f"[OANDA:{self.env}] {'BUY' if units > 0 else 'SELL'} {abs(units)} {instrument} "
            f"fill={fill_price} id={order_id}"
        )
        print(msg)
        return OrderResult(
            ok=ok,
            order_id=order_id or None,
            fill_price=fill_price,
            message=msg,
            raw=data,
            dry_run=False,
        )

    def close_position(self, pair: str) -> OrderResult:
        instrument = to_oanda_instrument(pair)
        try:
            data = self._request(
                "PUT",
                f"/accounts/{self.account_id}/positions/{instrument}/close",
                {"longUnits": "ALL", "shortUnits": "ALL"},
            )
        except Exception as e:
            return OrderResult(False, None, None, str(e), dry_run=False)
        return OrderResult(
            True,
            str(data.get("lastTransactionID")),
            None,
            f"Closed {instrument}",
            raw=data,
            dry_run=False,
        )

    def open_positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/accounts/{self.account_id}/openPositions")
        return data.get("positions") or []

    def account_summary(self) -> dict[str, Any]:
        data = self._request("GET", f"/accounts/{self.account_id}/summary")
        acct = data.get("account") or {}
        return {
            "mode": f"oanda_{self.env}",
            "balance": float(acct.get("balance", 0) or 0),
            "nav": float(acct.get("NAV", 0) or 0),
            "unrealizedPL": float(acct.get("unrealizedPL", 0) or 0),
            "openPositionCount": int(acct.get("openPositionCount", 0) or 0),
            "currency": acct.get("currency"),
        }


def get_broker(force_dry_run: bool = False) -> Broker:
    """
    Factory. Returns DryRunBroker unless EXECUTE_ENABLED=true and credentials exist.
    """
    enabled = os.environ.get("EXECUTE_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if force_dry_run or not enabled:
        return DryRunBroker()

    try:
        return OandaBroker()
    except ValueError as e:
        print(f"[execution] Falling back to dry-run: {e}")
        return DryRunBroker()
