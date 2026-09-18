"""
COT (Commitment of Traders) ingestion — CFTC "Legacy Futures Only" report,
large-speculator (non-commercial) positioning.

Free, weekly, no API key required — the CFTC publishes this data through a
public Socrata endpoint (publicreporting.cftc.gov). This is the natural
institutional complement to data/sentiment.py's IG retail sentiment:
retail-vs-institutional divergence (retail crowd heavily long while large
speculators are heavily short, or vice versa) is a stronger contrarian
signal than retail positioning alone. See engine/confidence.py for how the
two are blended and contrasted.

Key difference from data/sentiment.py: IG sentiment is a live-only snapshot
(no history), so it can only ever touch the newest bar. COT has a full
weekly history, so a point-in-time COT score *can* be computed for a given
report date without lookahead (see `as_of_index` on cot_index()) — useful
if this ever gets wired into the backtest rather than just live/replay.

Like data/sentiment.py, everything downstream should depend on the return
shape (a plain float score), not on the CFTC specifically.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "store"
DATA_DIR.mkdir(exist_ok=True)

# Public Socrata dataset: "Commitments of Traders (Legacy Report) - Futures Only"
_COT_ENDPOINT = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# CFTC contract market codes for the majors' underlying currency futures
# (CME/ICE). These identify a *currency* futures contract, not a pair —
# pairs combine one or two legs via `Instrument.cot_legs` in
# data/instruments.py.
CFTC_CODES = {
    "EUR": "099741",  # Euro FX (CME)
    "GBP": "096742",  # British Pound (CME)
    "JPY": "097741",  # Japanese Yen (CME)
    "AUD": "232741",  # Australian Dollar (CME)
    "CAD": "090741",  # Canadian Dollar (CME)
    "CHF": "092741",  # Swiss Franc (CME)
    "NZD": "112741",  # New Zealand Dollar (CME)
    "USD": "098662",  # US Dollar Index (ICE)
}

# Standard "COT Index" lookback (Larry Williams / Stephen Briese style) —
# ~3 years of weekly reports.
DEFAULT_LOOKBACK_WEEKS = 156

# CFTC updates this report weekly (published Friday, "as of" the prior
# Tuesday), so there's no point refetching more than a couple of times a
# week. A stale-but-present cache is also used as a fallback if a refetch
# fails, so a transient outage doesn't kill the COT overlay outright.
DEFAULT_CACHE_MAX_AGE_DAYS = 3.0


class CotUnavailable(Exception):
    """Raised when COT data can't be fetched (network error, bad code,
    unmapped pair, malformed response).

    Callers (e.g. the scoring engine) should catch this and fall back to
    pattern/context(+sentiment)-only scoring rather than blocking a signal.
    """


def _cache_path(currency_code: str) -> Path:
    return DATA_DIR / f"cot_{currency_code.upper()}.json"


def _save_cache(currency_code: str, records: list[dict]) -> None:
    _cache_path(currency_code).write_text(json.dumps(records))


def _load_cache(currency_code: str) -> list[dict] | None:
    path = _cache_path(currency_code)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def fetch_cot_history(
    currency: str,
    lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    cache_max_age_days: float = DEFAULT_CACHE_MAX_AGE_DAYS,
) -> list[dict]:
    """
    Fetch (and cache) the trailing `lookback_weeks` of weekly Legacy COT
    reports for one currency's futures contract.

    currency: one of CFTC_CODES' keys, e.g. "EUR", "JPY".

    Returns a list of dicts, oldest first:
        {"date": "2026-08-25", "noncommercial_long": 123456.0,
         "noncommercial_short": 98765.0, "open_interest": 456789.0,
         "net": 24691.0, "net_pct_oi": 5.4}

    Raises CotUnavailable if there's no cache to fall back on and the fetch
    fails (bad currency code, network error, empty/malformed response).
    """
    code = CFTC_CODES.get(currency.upper())
    if not code:
        raise CotUnavailable(
            f"No CFTC contract code for currency {currency.upper()!r}. "
            f"Known: {', '.join(CFTC_CODES)}"
        )

    cache_path = _cache_path(currency)
    if cache_path.exists():
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400.0
        if age_days < cache_max_age_days:
            cached = _load_cache(currency)
            if cached:
                return cached

    params = {
        "cftc_contract_market_code": code,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(lookback_weeks),
        "$select": (
            "report_date_as_yyyy_mm_dd,"
            "noncommercial_positions_long_all,"
            "noncommercial_positions_short_all,"
            "open_interest_all"
        ),
    }

    try:
        resp = requests.get(_COT_ENDPOINT, params=params, timeout=15)
    except requests.RequestException as e:
        stale = _load_cache(currency)
        if stale:
            return stale
        raise CotUnavailable(f"CFTC request failed for {currency}: {e}") from e

    if resp.status_code != 200:
        stale = _load_cache(currency)
        if stale:
            return stale
        raise CotUnavailable(
            f"CFTC request failed for {currency} ({resp.status_code}): {resp.text[:200]}"
        )

    try:
        rows = resp.json()
    except ValueError as e:
        stale = _load_cache(currency)
        if stale:
            return stale
        raise CotUnavailable(f"CFTC response for {currency} was not JSON: {e}") from e

    if not rows:
        stale = _load_cache(currency)
        if stale:
            return stale
        raise CotUnavailable(f"CFTC returned no rows for {currency} (code={code}).")

    records: list[dict] = []
    for row in reversed(rows):  # API gives newest-first; store oldest-first
        try:
            long_pos = float(row["noncommercial_positions_long_all"])
            short_pos = float(row["noncommercial_positions_short_all"])
            oi = float(row.get("open_interest_all") or 0)
        except (KeyError, ValueError, TypeError):
            continue
        net = long_pos - short_pos
        records.append(
            {
                "date": str(row["report_date_as_yyyy_mm_dd"])[:10],
                "noncommercial_long": long_pos,
                "noncommercial_short": short_pos,
                "open_interest": oi,
                "net": net,
                "net_pct_oi": (net / oi * 100.0) if oi else 0.0,
            }
        )

    if not records:
        stale = _load_cache(currency)
        if stale:
            return stale
        raise CotUnavailable(f"CFTC response for {currency} had no usable rows.")

    _save_cache(currency, records)
    return records


def cot_index(records: list[dict], as_of_index: int | None = None) -> float:
    """
    Classic "COT Index": where the current net position sits within its own
    trailing range, expressed 0-100.

        index = (net_now - min(net)) / (max(net) - min(net)) * 100   [over the lookback]

    100 = large speculators are at their most net-long for this currency
    within the lookback window; 0 = most net-short; 50 = dead center.

    Uses net-as-%-of-open-interest rather than raw contract counts so the
    index stays comparable as open interest drifts over the years.

    as_of_index: index into `records` to treat as "now", for point-in-time
    scoring without lookahead (e.g. a historical replay). Defaults to the
    most recent record.
    """
    if not records:
        raise CotUnavailable("No COT records to compute an index from.")

    i = len(records) - 1 if as_of_index is None else as_of_index
    window = records[: i + 1]
    if len(window) < 2:
        return 50.0  # not enough history yet — treat as neutral

    values = [r["net_pct_oi"] for r in window]
    now = values[-1]
    lo, hi = min(values), max(values)
    if hi == lo:
        return 50.0
    return (now - lo) / (hi - lo) * 100.0


def cot_index_to_score(index_0_100: float) -> float:
    """Rescale a 0-100 COT Index onto the engine's -100..+100 scale,
    centered on 50 = neutral (same scale as engine/context.py and
    data/sentiment.py's sentiment_to_score()).

    Positive = large specs sit near their most-net-long extreme for this
    currency (bullish lean); negative = near their most-net-short extreme.
    Unlike sentiment_to_score(), this is NOT contrarian — it's the smart
    money's own directional read.
    """
    idx = max(0.0, min(100.0, index_0_100))
    return max(-100.0, min(100.0, (idx - 50.0) * 2.0))


def leg_score(currency: str) -> float:
    """COT-index score (-100..+100) for a single currency's futures, as
    described in cot_index_to_score(). Raises CotUnavailable on failure."""
    records = fetch_cot_history(currency)
    return cot_index_to_score(cot_index(records))


def fetch_cot_score(pair: str) -> float:
    """
    Convenience wrapper: fetch COT history for every currency leg of `pair`
    (via the instrument registry's `cot_legs`) and return one directional
    score, -100 (large specs positioned bearish this pair) to +100
    (positioned bullish). Crosses average their two legs.

    Raises CotUnavailable if the pair has no COT mapping, or every leg's
    fetch fails. Callers in the scoring engine should catch this and fall
    back to pattern/context(+sentiment)-only scoring.
    """
    from instruments import get_instrument

    try:
        inst = get_instrument(pair)
    except KeyError as e:
        raise CotUnavailable(str(e)) from e

    legs = inst.cot_legs
    if not legs:
        raise CotUnavailable(
            f"Instrument {pair!r} ({inst.display_name}) has no COT mapping "
            f"(asset_class={inst.asset_class}). Pattern/context(+sentiment) "
            f"score will be used without a COT overlay."
        )

    leg_scores: list[float] = []
    errors: list[str] = []
    for currency, sign in legs:
        try:
            leg_scores.append(sign * leg_score(currency))
        except CotUnavailable as e:
            errors.append(str(e))

    if not leg_scores:
        raise CotUnavailable(f"All COT legs failed for {pair!r}: {'; '.join(errors)}")

    score = sum(leg_scores) / len(leg_scores)
    return max(-100.0, min(100.0, round(score, 1)))
