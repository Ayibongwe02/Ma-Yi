"""
Stage 1 — Data ingestion.

Fetches OHLC candle data for configured pairs/timeframes and stores it
locally as CSV so the backtester (Stage 4) never has to re-fetch. Also
exposes a function to grab the latest closed candle for live use (Stage 5).

Data source: yfinance (free, no API key). Swap out `fetch_history()` and
`fetch_latest_candle()` internals for OANDA/Twelve Data later without
changing the interface — everything downstream depends on the DataFrame
shape, not the source.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
except ImportError:  # optional — synthetic fallback still works offline
    yf = None

DATA_DIR = Path(__file__).parent / "store"
DATA_DIR.mkdir(exist_ok=True)

# yfinance interval codes. 1h/4h aren't both natively supported for FX with
# long history, so 4h is resampled from 1h.
_YF_INTERVAL_MAP = {
    "15m": "15m",
    "1h": "60m",
    "4h": "60m",  # fetched as 1h then resampled
    "1d": "1d",
}

# Timedelta for each supported timeframe — used to detect still-forming bars.
_TF_DELTA = {
    "15m": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1),
}


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    out = df.resample("4h").agg(agg).dropna(how="any")
    return out


def _drop_forming_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Drop the last row when it is still forming (not yet closed).

    yfinance (and most OHLC feeds) include the currently open bar. Alerting
    or training on that partial bar is unsafe — patterns and scores can
    change before the close. Mirror the conservative policy used by
    fetch_latest_candle(): only return fully closed candles.

    Logic:
      - If the last bar's open time + timeframe duration is still in the
        future (UTC), treat it as forming and drop it.
      - If we cannot parse times or have only one bar, fall back to dropping
        the last row when len > 1 (same as the previous fetch_latest_candle
        heuristic).
    """
    if df is None or df.empty or len(df) < 2:
        return df

    delta = _TF_DELTA.get(timeframe)
    if delta is None:
        # Unknown TF — conservative: drop last row
        return df.iloc[:-1].copy()

    try:
        last_open = df.index[-1]
        if not isinstance(last_open, pd.Timestamp):
            last_open = pd.Timestamp(last_open)
        # Normalize to UTC for comparison
        if last_open.tzinfo is None:
            last_open = last_open.tz_localize("UTC")
        else:
            last_open = last_open.tz_convert("UTC")

        now = pd.Timestamp.now(tz="UTC")
        # Bar is still forming if its close time is still in the future
        # (with a small 5s grace so we don't race the exact second of close)
        close_time = last_open + delta
        if now < close_time - pd.Timedelta(seconds=5):
            return df.iloc[:-1].copy()
        return df
    except Exception:
        # Fallback: always drop the most recent row when we have history
        return df.iloc[:-1].copy()


def fetch_history(pair: str, timeframe: str, period: str = "60d") -> pd.DataFrame:
    """
    Fetch historical OHLC candles for a pair/timeframe and cache to CSV.

    Always returns only *closed* candles (the still-forming bar is trimmed),
    matching the contract of fetch_latest_candle(). Downstream engines,
    live alerts, and ML training must never see a partial bar.

    pair: yfinance FX ticker, e.g. "EURUSD=X"
    timeframe: one of "15m", "1h", "4h", "1d"
    period: how far back to pull (yfinance limits intraday history, e.g.
            15m/60m intervals only go back ~60 days)
    """
    if timeframe not in _YF_INTERVAL_MAP:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    if yf is None:
        raise RuntimeError("yfinance is not installed; cannot fetch live history")

    yf_interval = _YF_INTERVAL_MAP[timeframe]
    ticker = yf.Ticker(pair)
    df = ticker.history(period=period, interval=yf_interval)

    if df.empty:
        raise RuntimeError(f"No data returned for {pair} @ {timeframe} (period={period})")

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index.name = "Datetime"

    if timeframe == "4h":
        df = _resample_to_4h(df)

    # Critical: never cache / return the still-forming candle
    df = _drop_forming_candle(df, timeframe)

    if df.empty:
        raise RuntimeError(
            f"No closed candles for {pair} @ {timeframe} after dropping forming bar"
        )

    _save(df, pair, timeframe)
    return df


def _cache_path(pair: str, timeframe: str) -> Path:
    safe_pair = pair.replace("=", "_").replace("/", "_")
    return DATA_DIR / f"{safe_pair}_{timeframe}.csv"


def _save(df: pd.DataFrame, pair: str, timeframe: str) -> None:
    df.to_csv(_cache_path(pair, timeframe))


def load_cached(pair: str, timeframe: str) -> pd.DataFrame:
    """Load previously cached candles without hitting the network.

    Still applies the forming-bar trim so a stale cache written before this
    fix (or mid-bar) never feeds an incomplete candle into charts/engine.
    """
    path = _cache_path(pair, timeframe)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached data for {pair} @ {timeframe}. Run fetch_history() first."
        )
    df = pd.read_csv(path, index_col="Datetime", parse_dates=True)
    return _drop_forming_candle(df, timeframe)


def fetch_latest_candle(pair: str, timeframe: str) -> pd.Series:
    """
    Return the most recent *closed* candle for a pair/timeframe.
    Used by the live delivery module (Stage 5) — pulls a short recent
    window rather than the full history for speed.

    Shares the same forming-bar trim as fetch_history() so live and
    historical paths never disagree on which bar is "current".
    """
    if yf is None:
        raise RuntimeError("yfinance is not installed; cannot fetch live candles")

    yf_interval = _YF_INTERVAL_MAP[timeframe]
    ticker = yf.Ticker(pair)
    df = ticker.history(period="5d", interval=yf_interval)
    if df.empty:
        raise RuntimeError(f"No recent data for {pair} @ {timeframe}")

    if timeframe == "4h":
        df = _resample_to_4h(df)

    df = _drop_forming_candle(df, timeframe)
    if df.empty:
        raise RuntimeError(f"No closed candles for {pair} @ {timeframe}")
    return df.iloc[-1]


def load_pairs_from_env() -> tuple[list[str], list[str]]:
    """Read PAIRS and TIMEFRAMES from environment (.env), with fallback defaults."""
    pairs = os.environ.get("PAIRS", "EURUSD=X,GBPUSD=X").split(",")
    timeframes = os.environ.get("TIMEFRAMES", "1h,4h").split(",")
    return [p.strip() for p in pairs], [t.strip() for t in timeframes]
