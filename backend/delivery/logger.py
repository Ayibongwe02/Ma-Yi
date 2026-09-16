"""
Signal logger — JSONL store for every evaluated signal.

Feeds the Stage 6 dashboard. Append-only file under delivery/logs/.
Falls back to /tmp/forex_signal_logs if the project path is not writable.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PRIMARY = Path(__file__).parent / "logs"
_FALLBACK = Path("/tmp/forex_signal_logs")

_lock = threading.Lock()
_next_id = 0
_log_path: Path | None = None


def _resolve_log_path() -> Path:
    global _log_path
    if _log_path is not None:
        return _log_path
    for candidate in (_PRIMARY, _FALLBACK):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test = candidate / ".write_test"
            test.write_text("ok")
            test.unlink(missing_ok=True)
            _log_path = candidate / "signals.jsonl"
            return _log_path
        except OSError:
            continue
    # last resort: in-memory only path still points somewhere
    _log_path = _FALLBACK / "signals.jsonl"
    _FALLBACK.mkdir(parents=True, exist_ok=True)
    return _log_path


def _load_all() -> list[dict[str, Any]]:
    path = _resolve_log_path()
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _rewrite(rows: list[dict[str, Any]]) -> None:
    path = _resolve_log_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    tmp.replace(path)


def init_db() -> None:
    """Ensure log directory and file exist; seed id counter."""
    global _next_id
    path = _resolve_log_path()
    rows = _load_all()
    if rows:
        _next_id = max(int(r.get("id", 0)) for r in rows) + 1
    else:
        _next_id = 1
        if not path.exists():
            path.touch()


def log_signal(
    pair: str,
    timeframe: str,
    bar_time: str | datetime,
    pattern: str,
    direction: int,
    final_score: float,
    fired: bool,
    *,
    raw_score: float | None = None,
    entry: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    risk: float | None = None,
    rr: float | None = None,
    trend: int | None = None,
    sr_score: float | None = None,
    volatility: str | None = None,
    outcome: str = "pending",
    alerted: bool = False,
    meta: dict | None = None,
) -> int:
    """Append one signal row. Returns the new row id."""
    global _next_id
    init_db()
    if isinstance(bar_time, datetime):
        bar_time = bar_time.isoformat()
    now = datetime.now(timezone.utc).isoformat()
    path = _resolve_log_path()

    with _lock:
        sid = _next_id
        _next_id += 1
        row = {
            "id": sid,
            "ts": now,
            "pair": pair,
            "timeframe": timeframe,
            "bar_time": str(bar_time),
            "pattern": pattern,
            "direction": int(direction),
            "raw_score": raw_score,
            "final_score": float(final_score),
            "fired": bool(fired),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk": risk,
            "rr": rr,
            "trend": trend,
            "sr_score": sr_score,
            "volatility": volatility,
            "outcome": outcome,
            "alerted": bool(alerted),
            "meta": meta,
            "created_at": now,
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except OSError:
            # Project FS can be flaky in some environments — switch to /tmp
            global _log_path
            _log_path = _FALLBACK / "signals.jsonl"
            _FALLBACK.mkdir(parents=True, exist_ok=True)
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
    return sid


def update_outcome(signal_id: int, outcome: str, auto_retrain: bool = True) -> None:
    """Set win/loss/pending on a signal. When outcome is closed and
    auto_retrain is True, triggers debounced ML retrain (fired+labeled only)."""
    with _lock:
        rows = _load_all()
        for r in rows:
            if int(r.get("id", -1)) == int(signal_id):
                r["outcome"] = outcome
                break
        _rewrite(rows)
    if auto_retrain and str(outcome).lower() in ("win", "loss"):
        try:
            from engine.ml.learner import auto_retrain as _ar
            _ar(reason=f"logger_outcome:{signal_id}:{outcome}")
        except Exception:
            pass


def mark_alerted(signal_id: int) -> None:
    with _lock:
        rows = _load_all()
        for r in rows:
            if int(r.get("id", -1)) == int(signal_id):
                r["alerted"] = True
                break
        _rewrite(rows)


def update_second_opinion(signal_id: int, opinion: dict[str, Any]) -> bool:
    """Attach a sister-system (Sentinel) second-opinion payload to a signal row.

    Stores under key ``second_opinion``. Returns True if the signal was found.
    """
    found = False
    with _lock:
        rows = _load_all()
        for r in rows:
            if int(r.get("id", -1)) == int(signal_id):
                r["second_opinion"] = opinion
                found = True
                break
        if found:
            _rewrite(rows)
    return found


def get_signals(
    pair: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    fired_only: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    init_db()
    rows = _load_all()
    out: list[dict[str, Any]] = []
    for r in rows:
        if pair and r.get("pair") != pair:
            continue
        bt = str(r.get("bar_time", ""))
        if from_date and bt < from_date:
            continue
        if to_date and bt > to_date:
            continue
        if fired_only and not r.get("fired"):
            continue
        out.append(r)
    out.sort(key=lambda x: str(x.get("bar_time", "")), reverse=True)
    return out[:limit]


def stats(pair: str | None = None) -> dict[str, Any]:
    """Aggregate win rate and counts for the dashboard."""
    rows = get_signals(pair=pair, fired_only=True, limit=100_000)
    wins = sum(1 for r in rows if r.get("outcome") == "win")
    losses = sum(1 for r in rows if r.get("outcome") == "loss")
    pending = sum(1 for r in rows if r.get("outcome") == "pending")
    closed = wins + losses
    win_rate = (wins / closed * 100) if closed else 0.0
    return {
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "closed": closed,
        "win_rate": round(win_rate, 1),
        "total_fired": wins + losses + pending,
    }


def get_log_path() -> Path:
    return _resolve_log_path()


# Back-compat alias used by dashboard
DB_PATH = property(lambda self: get_log_path())  # type: ignore

# module-level for simple import
def __getattr__(name: str):
    if name == "DB_PATH":
        return get_log_path()
    raise AttributeError(name)
