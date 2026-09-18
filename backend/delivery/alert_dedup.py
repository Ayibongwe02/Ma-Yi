"""
Alert de-duplication — prevents the same (pair, timeframe, bar) signal from
being re-sent to Telegram on every scan cycle.

Without this, a signal that fired once on a given closed candle would get
re-alerted on every subsequent auto-scan (default hourly) for as long as
that candle stays inside the lookback window, since the underlying scan
re-evaluates the whole window each time. Persisted to disk so it survives
process restarts (auto-scan runs in a background asyncio loop; a container
restart should not cause a burst of re-alerts for already-seen bars).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_PRIMARY = Path(__file__).parent / "logs"
_FALLBACK = Path("/tmp/forex_signal_logs")
_FILENAME = "last_alerted.json"

_lock = threading.Lock()
_state_path: Path | None = None


def _resolve_path() -> Path:
    global _state_path
    if _state_path is not None:
        return _state_path
    for candidate in (_PRIMARY, _FALLBACK):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test = candidate / ".write_test"
            test.write_text("ok")
            test.unlink(missing_ok=True)
            _state_path = candidate / _FILENAME
            return _state_path
        except OSError:
            continue
    _state_path = _FALLBACK / _FILENAME
    _FALLBACK.mkdir(parents=True, exist_ok=True)
    return _state_path


def _load() -> dict[str, Any]:
    path = _resolve_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(state: dict[str, Any]) -> None:
    path = _resolve_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    tmp.replace(path)


def _key(pair: str, timeframe: str) -> str:
    return f"{pair}|{timeframe}"


def already_alerted(pair: str, timeframe: str, bar_time: str) -> bool:
    """True if this exact bar (or a newer one) was already alerted for this
    pair/timeframe. Compares by string equality/ordering on ISO timestamps,
    which sort correctly."""
    with _lock:
        state = _load()
        last = state.get(_key(pair, timeframe))
        return last is not None and str(bar_time) <= str(last)


def mark_alerted(pair: str, timeframe: str, bar_time: str) -> None:
    with _lock:
        state = _load()
        state[_key(pair, timeframe)] = str(bar_time)
        _save(state)
