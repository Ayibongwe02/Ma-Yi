"""Smoke tests for the instrument registry and scale-aware helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from instruments import (
    get_instrument,
    to_oanda_instrument,
    scale_params,
    DEFAULT_TICKERS,
)


def main():
    assert len(DEFAULT_TICKERS) == 5
    eurusd = get_instrument("EURUSD=X")
    assert eurusd.oanda == "EUR_USD"
    assert eurusd.pip_size == 0.0001

    usdjpy = get_instrument("USDJPY=X")
    assert usdjpy.pip_size == 0.01
    assert usdjpy.round_increment == 0.50

    dji = get_instrument("^DJI")
    assert dji.oanda == "US30_USD"
    assert dji.asset_class == "index"
    assert dji.ig_market_id is None

    assert to_oanda_instrument("^DJI") == "US30_USD"
    assert to_oanda_instrument("GBPJPY=X") == "GBP_JPY"

    jpy_scale = scale_params("USDJPY=X")
    eur_scale = scale_params("EURUSD=X")
    assert jpy_scale["round_increment"] != eur_scale["round_increment"]
    assert jpy_scale["pip_size"] != eur_scale["pip_size"]

    print("instrument registry OK")


if __name__ == "__main__":
    main()
