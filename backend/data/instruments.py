"""
Instrument registry — single source of truth for tickers, OANDA codes,
pip/price scale, and scale-aware engine parameters.

Hardcoded absolute tolerances (e.g. tol=0.0008, round_increment=0.0050)
were tuned for ~1.xxxx majors. JPY pairs and indices need different
scales; every consumer should look up values here instead of guessing
from string patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Instrument:
    """One tradeable market the pipeline can run on."""

    ticker: str  # yfinance symbol, e.g. "EURUSD=X" or "^DJI"
    oanda: str  # OANDA instrument, e.g. "EUR_USD" or "US30_USD"
    display_name: str  # human-facing label
    asset_class: str  # "fx" | "index"
    pip_size: float  # conventional pip (FX) or point (index) size
    round_increment: float  # psychological level spacing for S/R filter
    # Relative tolerance for tweezer high/low matching:
    # abs(h1-h2)/max(h1, eps) must be < tweezer_tol_rel.
    # Derived from ~8 pips on a 1.0850 major → 8 * 0.0001 / 1.085 ≈ 0.00074.
    # Stored explicitly so JPY / index instruments don't inherit FX-major tuning.
    tweezer_tol_rel: float
    price_decimals: int  # rounding for entry/SL/TP display
    ig_market_id: str | None  # IG Client Sentiment market id, or None
    typical_price: float  # seed for synthetic candles / sanity checks
    # Approximate $ value of 1 pip/point per 1 unit of base (informational;
    # used by plain-language alerts for rough dollar-risk copy).
    # For FX majors quoted USD: ~$10 per standard lot per pip → per unit ~0.0001.
    # Kept simple; not used for live sizing (risk.py uses price distance).
    approx_usd_per_pip_per_unit: float = 1.0
    # CFTC Commitment of Traders mapping: which currency futures contract(s)
    # (see data/cot.py CFTC_CODES) drive this pair's institutional score, and
    # the sign of each leg's contribution. +1 means "large specs net long
    # this currency's futures" = bullish for the pair; -1 means the opposite
    # (e.g. USD is the base currency, so a net-long JPY reading is bearish
    # USDJPY). Crosses combine two legs; None means no COT mapping (indices).
    cot_legs: tuple[tuple[str, int], ...] | None = None


# Canonical set. Keys are the primary yfinance tickers used in PAIRS=.
_REGISTRY: dict[str, Instrument] = {}


def _register(inst: Instrument) -> Instrument:
    _REGISTRY[inst.ticker] = inst
    # Also index by bare FX code and OANDA form for flexible lookup.
    bare = inst.ticker.replace("=X", "").replace("=x", "")
    if bare != inst.ticker:
        _REGISTRY.setdefault(bare, inst)
    _REGISTRY.setdefault(inst.oanda, inst)
    _REGISTRY.setdefault(inst.oanda.replace("_", ""), inst)
    return inst


# --- FX majors (USD quote) ---
_register(
    Instrument(
        ticker="EURUSD=X",
        oanda="EUR_USD",
        display_name="EUR/USD",
        asset_class="fx",
        pip_size=0.0001,
        round_increment=0.0050,
        tweezer_tol_rel=0.0008,
        price_decimals=5,
        ig_market_id="EURUSD",
        typical_price=1.0850,
        approx_usd_per_pip_per_unit=0.0001,
        cot_legs=(("EUR", 1),),
    )
)
_register(
    Instrument(
        ticker="GBPUSD=X",
        oanda="GBP_USD",
        display_name="GBP/USD",
        asset_class="fx",
        pip_size=0.0001,
        round_increment=0.0050,
        tweezer_tol_rel=0.0008,
        price_decimals=5,
        ig_market_id="GBPUSD",
        typical_price=1.2700,
        approx_usd_per_pip_per_unit=0.0001,
        cot_legs=(("GBP", 1),),
    )
)

# --- JPY pairs (pip = 0.01) ---
_register(
    Instrument(
        ticker="USDJPY=X",
        oanda="USD_JPY",
        display_name="USD/JPY",
        asset_class="fx",
        pip_size=0.01,
        round_increment=0.50,
        # ~8 pips on ~150 → 0.08/150 ≈ 0.00053; use slightly looser 0.0007
        tweezer_tol_rel=0.0007,
        price_decimals=3,
        ig_market_id="USDJPY",
        typical_price=150.0,
        approx_usd_per_pip_per_unit=0.01 / 150.0,  # rough
        # USD is the base currency here, so net-long-JPY-futures (bullish
        # JPY) implies a bearish USDJPY — hence the -1 sign.
        cot_legs=(("JPY", -1),),
    )
)
_register(
    Instrument(
        ticker="GBPJPY=X",
        oanda="GBP_JPY",
        display_name="GBP/JPY",
        asset_class="fx",
        pip_size=0.01,
        round_increment=0.50,
        tweezer_tol_rel=0.0007,
        price_decimals=3,
        ig_market_id="GBPJPY",
        typical_price=190.0,
        approx_usd_per_pip_per_unit=0.01 / 190.0,
        # Cross pair: average the GBP leg (bullish when specs are net long
        # GBP) with the inverted JPY leg (bullish GBPJPY when specs are net
        # short JPY / long USD-vs-JPY-style weakness in the yen).
        cot_legs=(("GBP", 1), ("JPY", -1)),
    )
)

# --- Index ---
_register(
    Instrument(
        ticker="^DJI",
        oanda="US30_USD",
        display_name="US30 (Dow Jones)",
        asset_class="index",
        pip_size=1.0,  # 1 index point
        round_increment=100.0,  # big-figure levels e.g. 39000, 39100
        # ~8 points on ~39000 → 8/39000 ≈ 0.0002; allow a bit more for noise
        tweezer_tol_rel=0.0003,
        price_decimals=2,
        ig_market_id=None,  # no reliable IG retail sentiment for indices here
        typical_price=39000.0,
        approx_usd_per_pip_per_unit=1.0,  # $1 per point per unit (CFD-style approx)
        cot_legs=None,  # no FX-currency COT mapping for an equity index here
    )
)

# Extra common aliases users might type
_REGISTRY.setdefault("US30", _REGISTRY["^DJI"])
_REGISTRY.setdefault("US30_USD", _REGISTRY["^DJI"])
_REGISTRY.setdefault("DJI", _REGISTRY["^DJI"])
_REGISTRY.setdefault("GBPJPY", _REGISTRY["GBPJPY=X"])
_REGISTRY.setdefault("USDJPY", _REGISTRY["USDJPY=X"])


DEFAULT_TICKERS: list[str] = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "GBPJPY=X",
    "^DJI",
]


def get_instrument(key: str) -> Instrument:
    """
    Resolve a ticker / OANDA code / alias to an Instrument.

    Raises KeyError with a helpful message if unknown.
    """
    if not key:
        raise KeyError("Empty instrument key")
    # Direct hit
    if key in _REGISTRY:
        return _REGISTRY[key]
    # Case-insensitive + light normalisation
    candidates = [
        key,
        key.upper(),
        key.replace("/", ""),
        key.upper().replace("/", "").replace("_", ""),
        key.upper().replace("=X", "") + "=X" if not key.endswith("=X") else key,
    ]
    for c in candidates:
        if c in _REGISTRY:
            return _REGISTRY[c]
        # try adding =X for bare FX codes
        if len(c) == 6 and c.isalpha() and f"{c}=X" in _REGISTRY:
            return _REGISTRY[f"{c}=X"]
    known = ", ".join(sorted({i.ticker for i in unique_instruments()}))
    raise KeyError(f"Unknown instrument {key!r}. Known tickers: {known}")


def unique_instruments() -> list[Instrument]:
    """Deduplicated list of registered instruments (by ticker)."""
    seen: set[str] = set()
    out: list[Instrument] = []
    for inst in _REGISTRY.values():
        if inst.ticker not in seen:
            seen.add(inst.ticker)
            out.append(inst)
    return out


def all_tickers() -> list[str]:
    return [i.ticker for i in unique_instruments()]


def to_oanda_instrument(pair: str) -> str:
    """Resolve any supported key to the OANDA instrument code."""
    try:
        return get_instrument(pair).oanda
    except KeyError:
        # Fallback for unlisted FX-style pairs: EURUSD=X → EUR_USD
        p = pair.upper().replace("=X", "").replace("=x", "").replace("/", "").replace("_", "")
        if len(p) == 6 and p.isalpha():
            return f"{p[:3]}_{p[3:]}"
        return pair


def scale_params(pair: str) -> dict:
    """
    Engine scale knobs for a pair: round_increment, tweezer_tol_rel,
    price_decimals, pip_size. Safe defaults for unknown keys mirror EUR/USD.
    """
    try:
        inst = get_instrument(pair)
        return {
            "round_increment": inst.round_increment,
            "tweezer_tol_rel": inst.tweezer_tol_rel,
            "price_decimals": inst.price_decimals,
            "pip_size": inst.pip_size,
            "display_name": inst.display_name,
            "asset_class": inst.asset_class,
            "ig_market_id": inst.ig_market_id,
            "typical_price": inst.typical_price,
            "approx_usd_per_pip_per_unit": inst.approx_usd_per_pip_per_unit,
            "cot_legs": inst.cot_legs,
        }
    except KeyError:
        return {
            "round_increment": 0.0050,
            "tweezer_tol_rel": 0.0008,
            "price_decimals": 5,
            "pip_size": 0.0001,
            "display_name": pair,
            "asset_class": "fx",
            "ig_market_id": None,
            "typical_price": 1.0,
            "approx_usd_per_pip_per_unit": 0.0001,
            "cot_legs": None,
        }


def list_for_env(tickers: Iterable[str] | None = None) -> str:
    """Comma-separated ticker string suitable for PAIRS=."""
    return ",".join(tickers or DEFAULT_TICKERS)
