"""
Sentiment ingestion — IG Client Sentiment API.

Fetches retail long/short positioning percentages for configured pairs and
converts them into a contrarian score on the same -100..+100 scale used by
the pattern/context engine (engine/context.py). Retail crowds tend to lean
the wrong way at extremes, so a heavily long crowd nudges the score bearish,
and a heavily short crowd nudges it bullish.

Data source: IG's REST API (labs.ig.com). Requires a free IG account (a demo
account works, no funding needed) and API credentials. This module logs in
to get a session, then calls the /clientsentiment endpoint.

Like data/candles.py, everything downstream should depend on the return
shape (a plain dict / float), not on IG specifically — if you ever swap in
Myfxbook or OANDA's order book instead, only this file should need to change.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "store"
DATA_DIR.mkdir(exist_ok=True)

# IG REST API hosts
_IG_HOSTS = {
    "demo": "https://demo-api.ig.com/gateway/deal",
    "live": "https://api.ig.com/gateway/deal",
}

# IG client sentiment is keyed by "market ID", not a plain pair string like
# "EURUSD=X". Prefer the instrument registry; keep this map as a fallback
# for pairs not yet registered (and for tests that don't load instruments).
_PAIR_TO_IG_MARKET_ID = {
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "GBPJPY=X": "GBPJPY",
    "AUDUSD=X": "AUDUSD",
    "USDCAD=X": "USDCAD",
    # Indices: no IG retail-sentiment mapping — registry returns None and
    # confidence blending passes the pattern score through unchanged.
}

_SESSION_CACHE: dict = {}  # in-memory only; re-login each process start


class SentimentUnavailable(Exception):
    """Raised when sentiment can't be fetched (missing creds, API error, etc).

    Callers (e.g. the scoring engine) should catch this and fall back to
    pattern/context-only scoring rather than blocking a signal on sentiment.
    """


def _ig_config() -> dict:
    api_key = os.environ.get("IG_API_KEY", "")
    username = os.environ.get("IG_USERNAME", "")
    password = os.environ.get("IG_PASSWORD", "")
    env = os.environ.get("IG_ENV", "demo").strip().lower()

    if not (api_key and username and password):
        raise SentimentUnavailable(
            "IG credentials not set (IG_API_KEY / IG_USERNAME / IG_PASSWORD). "
            "Sign up for a free IG account and add them to .env to enable "
            "sentiment data."
        )
    if env not in _IG_HOSTS:
        raise SentimentUnavailable(f"Invalid IG_ENV={env!r}; must be 'demo' or 'live'.")

    return {"api_key": api_key, "username": username, "password": password, "env": env}


def _login(config: dict) -> str:
    """Log in to IG and return a bearer-style auth token (cached in-process)."""
    cache_key = (config["env"], config["username"])
    cached = _SESSION_CACHE.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    host = _IG_HOSTS[config["env"]]
    resp = requests.post(
        f"{host}/session",
        headers={
            "X-IG-API-KEY": config["api_key"],
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "2",
        },
        data=json.dumps({"identifier": config["username"], "password": config["password"]}),
        timeout=10,
    )
    if resp.status_code != 200:
        raise SentimentUnavailable(
            f"IG login failed ({resp.status_code}): {resp.text[:200]}"
        )

    # IG v2 session login returns the tokens in response headers, not the body.
    cst = resp.headers.get("CST")
    security_token = resp.headers.get("X-SECURITY-TOKEN")
    if not (cst and security_token):
        raise SentimentUnavailable("IG login succeeded but tokens were missing from headers.")

    token = f"{cst}::{security_token}"
    _SESSION_CACHE[cache_key] = {"token": token, "expires_at": time.time() + 55 * 60}
    return token


def _market_id_for(pair: str) -> str:
    # Registry first (handles aliases like GBPJPY, US30, ^DJI).
    try:
        from instruments import get_instrument

        inst = get_instrument(pair)
        if inst.ig_market_id:
            return inst.ig_market_id
        raise SentimentUnavailable(
            f"Instrument {pair!r} ({inst.display_name}) has no IG sentiment "
            f"mapping (asset_class={inst.asset_class}). Pattern/context score "
            f"will be used without sentiment."
        )
    except ImportError:
        pass
    except KeyError:
        pass

    market_id = _PAIR_TO_IG_MARKET_ID.get(pair)
    if not market_id:
        raise SentimentUnavailable(
            f"No IG market ID mapping for pair {pair!r}. Add it to the "
            f"instrument registry (data/instruments.py) or "
            f"_PAIR_TO_IG_MARKET_ID in data/sentiment.py."
        )
    return market_id


def _cache_path(pair: str) -> Path:
    safe_pair = pair.replace("=", "_").replace("/", "_")
    return DATA_DIR / f"sentiment_{safe_pair}.json"


def fetch_sentiment(pair: str) -> dict:
    """
    Fetch current retail long/short positioning for a pair from IG.

    pair: our existing pair naming, e.g. "EURUSD=X" (same as candles.py).

    Returns a dict:
        {
            "pair": "EURUSD=X",
            "ig_market_id": "EURUSD",
            "long_pct": 62.0,
            "short_pct": 38.0,
            "fetched_at": 1234567890.0,
        }

    Raises SentimentUnavailable on any failure (missing creds, network error,
    unmapped pair, malformed response). Callers should catch this.
    """
    config = _ig_config()
    market_id = _market_id_for(pair)
    token = _login(config)
    cst, security_token = token.split("::", 1)
    host = _IG_HOSTS[config["env"]]

    resp = requests.get(
        f"{host}/clientsentiment/{market_id}",
        headers={
            "X-IG-API-KEY": config["api_key"],
            "CST": cst,
            "X-SECURITY-TOKEN": security_token,
            "Accept": "application/json; charset=UTF-8",
            "Version": "1",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise SentimentUnavailable(
            f"IG clientsentiment request failed ({resp.status_code}): {resp.text[:200]}"
        )

    body = resp.json()
    long_pct = body.get("longPositionPercentage")
    short_pct = body.get("shortPositionPercentage")
    if long_pct is None or short_pct is None:
        raise SentimentUnavailable(f"Unexpected IG sentiment response shape: {body}")

    result = {
        "pair": pair,
        "ig_market_id": market_id,
        "long_pct": float(long_pct),
        "short_pct": float(short_pct),
        "fetched_at": time.time(),
    }
    _save_cache(pair, result)
    return result


def _save_cache(pair: str, result: dict) -> None:
    _cache_path(pair).write_text(json.dumps(result, indent=2))


def load_cached_sentiment(pair: str) -> dict:
    """Load the last-fetched sentiment for a pair without hitting the network."""
    path = _cache_path(pair)
    if not path.exists():
        raise SentimentUnavailable(
            f"No cached sentiment for {pair}. Call fetch_sentiment() first."
        )
    return json.loads(path.read_text())


def sentiment_to_score(long_pct: float) -> float:
    """
    Convert a retail long % into a contrarian score from -100 (bearish) to
    +100 (bullish), on the same scale the pattern/context engine uses.

    50% long/short is neutral (score 0). 100% long -> score -100 (fully
    contrarian bearish). 0% long (100% short) -> score +100.
    """
    long_pct = max(0.0, min(100.0, long_pct))
    return (50.0 - long_pct) * 2.0


def fetch_sentiment_score(pair: str) -> float:
    """
    Convenience wrapper: fetch live sentiment for a pair and return just the
    contrarian score (-100..+100). Raises SentimentUnavailable on failure —
    callers in the scoring engine should catch this and fall back to
    pattern/context-only scoring rather than blocking a signal.
    """
    data = fetch_sentiment(pair)
    return sentiment_to_score(data["long_pct"])
