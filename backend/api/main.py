"""Ma-yi FastAPI bridge for React desk (Stage 2/3)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "delivery"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "execution"))
sys.path.insert(0, str(ROOT / "engine"))

from logger import get_signals, stats, init_db, update_outcome, update_second_opinion  # noqa: E402
from plain_language import confidence_label, display_pair, glossary_markdown  # noqa: E402

try:
    from engine.ml.learner import get_learner
except Exception:
    get_learner = None  # type: ignore

app = FastAPI(title="Ma-yi Live Command API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENGINE_LOG = ROOT / "delivery" / "logs" / "engine.log"
ORDERS_LOG = ROOT / "execution" / "logs" / "orders.jsonl"
KILL_SWITCH = ROOT / "execution" / "logs" / "kill_switch.flag"
BACKTEST_DIR = ROOT / "backtest" / "reports"
WATCH_LIST = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GBPJPY=X", "^DJI"]
WATCH_LABELS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "GBPJPY=X": "GBP/JPY",
    "^DJI": "US30",
}
init_db()

try:
    from api.pair_bias import refresh_pair_bias, load_pair_bias
except Exception:
    try:
        from pair_bias import refresh_pair_bias, load_pair_bias  # type: ignore
    except Exception:
        refresh_pair_bias = None  # type: ignore

        def load_pair_bias():  # type: ignore
            return []

DIST = ROOT / "frontend" / "dist"


class KillSwitchBody(BaseModel):
    enabled: bool


class OutcomeBody(BaseModel):
    outcome: str


class SecondOpinionBody(BaseModel):
    """Payload from sister systems (Sentinel)."""
    final_verdict: str  # CONFIRM | CAUTION | VETO
    combined_score: float = 0.0
    rationale: str = ""
    analyzers: list = []
    source: str = "sentinel"
    trade_id: int | None = None
    pair: str | None = None
    direction: int | None = None
    mayi_score: float | None = None
    extra: dict | None = None


class ScanBody(BaseModel):
    pair: Optional[str] = None
    timeframe: str = "1h"
    mode: str = "replay"  # replay | live
    period: str = "5d"
    refresh_bias: bool = True
    # Deprecated — scans are never gated on ML health. Accepted and ignored
    # so older clients keep working.
    force: bool = False


def _read_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return []
    return rows[-limit:][::-1]


def _tail_text(path: Path, max_bytes: int = 80000) -> str:
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _enrich(s: dict) -> dict:
    s = dict(s)
    s["display_pair"] = display_pair(s.get("pair", ""))
    s["confidence"] = confidence_label(float(s.get("final_score") or 0))
    if get_learner:
        try:
            pred = get_learner().predict(s)
            s["ml_prob_win"] = pred.get("prob_win")
            s["ml_adjusted_score"] = pred.get("adjusted_score")
            s["ml_anomaly"] = pred.get("anomaly")
            s["ml_note"] = pred.get("note")
        except Exception:
            pass
    # Sister-system (Sentinel) second opinion, if previously POSTed
    so = s.get("second_opinion")
    if isinstance(so, dict):
        s["second_opinion"] = so
        s["sentinel_verdict"] = so.get("final_verdict")
        s["sentinel_score"] = so.get("combined_score")
        s["sentinel_rationale"] = so.get("rationale")
    return s


class TapeManager:
    """Fan-out for the Stage 3 live tape: broadcasts new signals, orders,
    engine-log lines, and kill-switch flips to every connected WebSocket
    client, and replays a short history to anyone who joins mid-stream."""

    def __init__(self, history_len: int = 200):
        self.clients: set[WebSocket] = set()
        self.history: deque = deque(maxlen=history_len)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)
        for evt in list(self.history):
            try:
                await ws.send_json(evt)
            except Exception:
                pass

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, event: dict) -> None:
        self.history.append(event)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.clients.discard(ws)


tape = TapeManager()
_tape_state: dict[str, Any] = {"signals_pos": 0, "orders_pos": 0, "log_pos": 0, "kill": None}


async def _poll_tape_once() -> None:
    signals_path = ROOT / "delivery" / "logs" / "signals.jsonl"
    if signals_path.exists():
        size = signals_path.stat().st_size
        if size > _tape_state["signals_pos"]:
            with open(signals_path, "r", encoding="utf-8") as f:
                f.seek(_tape_state["signals_pos"])
                new = f.read()
                _tape_state["signals_pos"] = f.tell()
            for line in new.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await tape.broadcast({
                    "type": "signal",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": _enrich(row),
                })

    if ORDERS_LOG.exists():
        size = ORDERS_LOG.stat().st_size
        if size > _tape_state["orders_pos"]:
            with open(ORDERS_LOG, "r", encoding="utf-8") as f:
                f.seek(_tape_state["orders_pos"])
                new = f.read()
                _tape_state["orders_pos"] = f.tell()
            for line in new.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await tape.broadcast({
                    "type": "order",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": row,
                })

    if ENGINE_LOG.exists():
        size = ENGINE_LOG.stat().st_size
        if size > _tape_state["log_pos"]:
            with open(ENGINE_LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(_tape_state["log_pos"])
                new = f.read()
                _tape_state["log_pos"] = f.tell()
            for line in new.splitlines():
                line = line.strip()
                if line:
                    await tape.broadcast({
                        "type": "log",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "line": line,
                    })

    now_kill = KILL_SWITCH.exists()
    if now_kill != _tape_state["kill"]:
        _tape_state["kill"] = now_kill
        await tape.broadcast({
            "type": "kill_switch",
            "ts": datetime.now(timezone.utc).isoformat(),
            "enabled": now_kill,
        })


async def _tape_watcher() -> None:
    # Prime read offsets to end-of-file so we stream only new events from
    # the moment the API comes up, not the whole backlog in one burst.
    signals_path = ROOT / "delivery" / "logs" / "signals.jsonl"
    _tape_state["signals_pos"] = signals_path.stat().st_size if signals_path.exists() else 0
    _tape_state["orders_pos"] = ORDERS_LOG.stat().st_size if ORDERS_LOG.exists() else 0
    _tape_state["log_pos"] = ENGINE_LOG.stat().st_size if ENGINE_LOG.exists() else 0
    _tape_state["kill"] = KILL_SWITCH.exists()
    while True:
        try:
            await _poll_tape_once()
        except Exception as e:
            try:
                await tape.broadcast({
                    "type": "error",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "message": str(e),
                })
            except Exception:
                pass
        await asyncio.sleep(1.5)


async def _auto_retrain_loop() -> None:
    """Periodically retrain when new labeled outcomes appear (env-gated)."""
    from engine.ml.learner import auto_retrain, _interval_sec, _auto_retrain_enabled
    # Stagger first check so startup scan can finish first
    await asyncio.sleep(20)
    while True:
        try:
            if _auto_retrain_enabled() and get_learner:
                result = auto_retrain(reason="interval")
                if result.get("ok"):
                    await tape.broadcast({
                        "type": "log",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "line": (
                            f"[ml] auto-retrain (interval) n={result.get('n_samples')} "
                            f"acc={result.get('train_accuracy')}"
                        ),
                    })
        except Exception as e:
            try:
                await tape.broadcast({
                    "type": "log",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "line": f"[ml] auto-retrain interval error: {e}",
                })
            except Exception:
                pass
        try:
            from engine.ml.learner import _interval_sec as _iv
            delay = _iv()
        except Exception:
            delay = 300.0
        await asyncio.sleep(delay)


def _auto_scan_enabled() -> bool:
    # Default TRUE at the code level. This used to default to "false",
    # which only worked out because every shipped deployment path
    # (Dockerfile, docker-compose, render.yaml) separately set
    # AUTO_SCAN_ENABLED=true as an env var. Any other way of running this
    # service — bare `uvicorn api.main:app`, a different PaaS, a systemd
    # unit, a fresh Render service created without the blueprint — skips
    # all of those files, so the unset env var silently fell back to "off"
    # with no error anywhere. That's exactly how the engine can look
    # broken ("no signals ever fire") for no reason a user could see from
    # the app itself. Now the app scans by default regardless of how it's
    # launched; an operator has to opt OUT with AUTO_SCAN_ENABLED=false,
    # not opt in.
    return os.environ.get("AUTO_SCAN_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _auto_scan_interval_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("AUTO_SCAN_INTERVAL_SEC", "3600")))
    except ValueError:
        return 300.0


# In-memory auto-scan status so the UI can show a loading state without
# polling engine.log. Updated by the background loop and exposed on /api/health.
_auto_scan_state: dict[str, Any] = {
    "enabled": True,
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_ok": None,
    "last_error": None,
    "next_due": None,
    "interval_sec": 3600.0,
}


def get_auto_scan_status() -> dict[str, Any]:
    st = dict(_auto_scan_state)
    st["enabled"] = _auto_scan_enabled()
    st["interval_sec"] = _auto_scan_interval_sec()
    return st


async def _auto_scan_loop() -> None:
    """Recurring engine refresh: reruns the scan on a fixed interval so the
    engines (signals, pair bias, ML model) keep working off fresh data
    without anyone needing to click "Scan now". Env-gated, ON by default —
    disable with AUTO_SCAN_ENABLED=false.

    Env knobs:
      AUTO_SCAN_ENABLED       true/false        (default: true)
      AUTO_SCAN_INTERVAL_SEC  seconds >= 30      (default: 3600 / 1 hour)
      AUTO_SCAN_MODE          replay|live        (default: RUNNER_MODE or replay)
      AUTO_SCAN_TIMEFRAME     e.g. 1h            (default: 1h)
      AUTO_SCAN_PERIOD        e.g. 5d            (default: 5d)
      AUTO_SCAN_PAIR          single pair, optional (default: all watchlist pairs)
      AUTO_SCAN_ON_START      true/false        (default: true — run the
                                                  first pass right after boot
                                                  instead of waiting a full
                                                  interval)
    """
    # Announce the effective config loudly at boot, in stdout/the process
    # logs directly — not just via /api/health — so whoever is running this
    # (however they're running it) can see immediately whether scanning is
    # actually on, without needing to know to check an endpoint first.
    print(
        f"[auto-scan] AUTO_SCAN_ENABLED={_auto_scan_enabled()} "
        f"interval={_auto_scan_interval_sec()}s "
        f"mode={os.environ.get('AUTO_SCAN_MODE', os.environ.get('RUNNER_MODE', 'replay'))} "
        f"timeframe={os.environ.get('AUTO_SCAN_TIMEFRAME', '1h')} "
        f"period={os.environ.get('AUTO_SCAN_PERIOD', '5d')}"
        + ("" if _auto_scan_enabled() else " — scanning is OFF (set AUTO_SCAN_ENABLED=true to enable)"),
        flush=True,
    )
    # Give the API a moment to finish booting before the first pass, then
    # decide whether that first pass runs immediately or waits a full
    # interval. AUTO_SCAN_ON_START is set in every shipped deployment
    # config (Dockerfile/docker-compose/render.yaml) but nothing in the
    # code ever read it — same class of bug as AUTO_SCAN_ENABLED's old
    # default: a knob that looks wired up but silently does nothing.
    # Defaults true (fresh data as soon as the app boots), matching what
    # those configs already assume it does.
    await asyncio.sleep(15)
    on_start = os.environ.get("AUTO_SCAN_ON_START", "true").strip().lower() in ("1", "true", "yes", "on")
    if not on_start and _auto_scan_enabled():
        interval = _auto_scan_interval_sec()
        _auto_scan_state["interval_sec"] = interval
        next_due = datetime.now(timezone.utc).timestamp() + interval
        _auto_scan_state["next_due"] = datetime.fromtimestamp(next_due, tz=timezone.utc).isoformat()
        await asyncio.sleep(interval)
    while True:
        interval = _auto_scan_interval_sec()
        _auto_scan_state["interval_sec"] = interval
        _auto_scan_state["enabled"] = _auto_scan_enabled()
        if not _auto_scan_enabled():
            _auto_scan_state["running"] = False
            _auto_scan_state["next_due"] = None
            await asyncio.sleep(interval)
            continue
        # NOTE: auto-scan is never gated on ML health. Scanning is how new
        # closed outcomes (labels) are produced, so blocking it when the model
        # looks overfit is circular — it removes the only source of the data
        # that would fix the model. Overfitting is handled inside the learner
        # by regularising hyperparameters, not by stopping the feed.
        mode = os.environ.get("AUTO_SCAN_MODE", os.environ.get("RUNNER_MODE", "replay"))
        timeframe = os.environ.get("AUTO_SCAN_TIMEFRAME", "1h")
        period = os.environ.get("AUTO_SCAN_PERIOD", "5d")
        pair = os.environ.get("AUTO_SCAN_PAIR") or None
        started = datetime.now(timezone.utc).isoformat()
        _auto_scan_state.update({
            "running": True,
            "last_started": started,
            "last_error": None,
        })
        try:
            await tape.broadcast({
                "type": "auto_scan",
                "ts": started,
                "status": "started",
                "line": f"[auto-scan] started mode={mode} tf={timeframe}",
            })
        except Exception:
            pass
        try:
            result = await asyncio.to_thread(
                _run_scan,
                pair=pair,
                timeframe=timeframe,
                mode=mode,
                period=period,
                refresh_bias=True,
                reason="auto",
            )
            finished = datetime.now(timezone.utc).isoformat()
            # A process-level success (ok=True) can still mean the scan got
            # no fresh data for one or more pairs (data_ok=False, see
            # _run_scan). Surface that as last_error too — otherwise
            # last_ok=true papers over a live data outage indefinitely.
            last_ok = bool(result.get("ok")) and bool(result.get("data_ok", True))
            last_error = None
            if not result.get("ok"):
                last_error = str(result.get("error") or result.get("detail") or "failed")
            elif result.get("warning"):
                last_error = str(result.get("warning"))
            _auto_scan_state.update({
                "running": False,
                "last_finished": finished,
                "last_ok": last_ok,
                "last_error": last_error,
            })
            summary_line = (
                f"[auto-scan] rerun complete ok={result.get('ok')} "
                f"fired={result.get('fired')} "
                f"bias_count={result.get('bias_count')} "
                f"next in {int(interval)}s"
            )
            if result.get("warning"):
                summary_line += f" | WARNING: {result.get('warning')}"
            await tape.broadcast({
                "type": "auto_scan",
                "ts": finished,
                "status": "finished",
                "ok": result.get("ok"),
                "data_ok": result.get("data_ok"),
                "warning": result.get("warning"),
                "line": summary_line,
            })
            await tape.broadcast({
                "type": "log",
                "ts": finished,
                "line": summary_line,
            })
        except Exception as e:
            finished = datetime.now(timezone.utc).isoformat()
            _auto_scan_state.update({
                "running": False,
                "last_finished": finished,
                "last_ok": False,
                "last_error": str(e),
            })
            try:
                await tape.broadcast({
                    "type": "auto_scan",
                    "ts": finished,
                    "status": "error",
                    "line": f"[auto-scan] error: {e}",
                })
                await tape.broadcast({
                    "type": "log",
                    "ts": finished,
                    "line": f"[auto-scan] error: {e}",
                })
            except Exception:
                pass
        # Schedule next due for UI countdown
        next_due = datetime.now(timezone.utc).timestamp() + interval
        _auto_scan_state["next_due"] = datetime.fromtimestamp(next_due, tz=timezone.utc).isoformat()
        await asyncio.sleep(interval)


@app.on_event("startup")
async def _start_tape_watcher() -> None:
    asyncio.create_task(_tape_watcher())
    asyncio.create_task(_auto_retrain_loop())
    asyncio.create_task(_auto_scan_loop())


@app.websocket("/ws/tape")
async def ws_tape(websocket: WebSocket) -> None:
    await tape.connect(websocket)
    try:
        while True:
            # We don't expect the client to send anything — this just keeps
            # the connection open and detects disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await tape.disconnect(websocket)
    except Exception:
        await tape.disconnect(websocket)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
        "kill_switch": KILL_SWITCH.exists(),
        "ml_available": get_learner is not None,
        "spa": DIST.exists() and (DIST / "index.html").exists(),
        "scan_owned": True,
        "auto_scan_enabled": _auto_scan_enabled(),
        "auto_scan_interval_sec": _auto_scan_interval_sec(),
        "auto_scan": get_auto_scan_status(),
    }


@app.get("/api/watchlist")
def watchlist():
    return [{"pair": p, "label": WATCH_LABELS.get(p, p)} for p in WATCH_LIST]


@app.get("/api/signals")
def signals(
    pair: Optional[str] = None,
    fired_only: bool = False,
    limit: int = Query(100, le=1000),
):
    return [_enrich(r) for r in get_signals(pair=pair, fired_only=fired_only, limit=limit)]


def _signal_age_sec(bar_time: Any) -> Optional[float]:
    """Seconds between now (UTC) and a signal's bar_time. None if unparseable."""
    import pandas as pd

    if not bar_time:
        return None
    try:
        ts = pd.Timestamp(bar_time)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        return float((pd.Timestamp.now(tz="UTC") - ts).total_seconds())
    except Exception:
        return None


@app.get("/api/signals/top")
def signals_top(
    n: int = Query(5, ge=1, le=50),
    pair: Optional[str] = None,
    open_only: bool = True,
):
    """
    Top-N live/open signal candidates sorted by final_score (desc by abs).
    Used by sister systems (e.g. Sentinel) as a clean contract for the
    highest-conviction candidates without dumping the entire log.
    """
    rows = get_signals(pair=pair, fired_only=True, limit=500)
    if open_only:
        # Prefer signals that are still open / pending outcome
        open_rows = [r for r in rows if r.get("outcome") in (None, "pending", "")]
        if open_rows:
            rows = open_rows
    # A "pending" signal can stay pending forever in live mode (outcome is
    # only ever simulated during replay — see runner.process_dataframe).
    # Ranking purely by score let an old pending signal that never got a
    # simulated win/loss outrank — and get returned ahead of — a genuinely
    # fresh one, so a caller reading this endpoint for "the current setup"
    # on a pair could be handed entry/sl/tp computed off a candle several
    # days old. Drop anything whose bar is stale relative to its own
    # timeframe before ranking by score, same freshness rule the candles
    # endpoint uses (one bar + weekend allowance).
    fresh_rows = []
    for r in rows:
        age = _signal_age_sec(r.get("bar_time"))
        max_age = _max_candle_age_sec(str(r.get("timeframe") or "1h"))
        if age is None or age <= max_age:
            fresh_rows.append(r)
    if fresh_rows:
        rows = fresh_rows
    # Sort by absolute final_score descending (strongest conviction first)
    rows.sort(key=lambda r: abs(float(r.get("final_score") or 0)), reverse=True)
    return [_enrich(r) for r in rows[:n]]


@app.get("/api/stats")
def api_stats(pair: Optional[str] = None):
    return stats(pair=pair)


@app.get("/api/live-command")
def live_command():
    all_sigs = get_signals(fired_only=True, limit=300)
    act_now, watch, noise = [], [], []
    for s in all_sigs:
        score = abs(float(s.get("final_score") or 0))
        e = _enrich(s)
        if score >= 70:
            act_now.append(e)
        elif score >= 50:
            watch.append(e)
        elif score >= 40:
            noise.append(e)
    longs = sum(1 for s in all_sigs if int(s.get("direction") or 0) > 0)
    shorts = sum(1 for s in all_sigs if int(s.get("direction") or 0) < 0)
    pair_bias = []
    try:
        pair_bias = load_pair_bias() if load_pair_bias else []
    except Exception:
        pair_bias = []
    return {
        "act_now": act_now[:20],
        "watch": watch[:30],
        "noise": noise[:20],
        "bias": {"longs": longs, "shorts": shorts, "net": longs - shorts},
        "pair_bias": pair_bias,
        "stats": stats(),
        "watchlist": [{"pair": p, "label": WATCH_LABELS.get(p, p)} for p in WATCH_LIST],
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/backtest/comparison")
def backtest_comparison():
    path = BACKTEST_DIR / "comparison.csv"
    if not path.exists():
        return []
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/api/backtest/per-pattern")
def backtest_per_pattern():
    path = BACKTEST_DIR / "per_pattern.csv"
    if not path.exists():
        return []
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/api/backtest/score-distribution")
def score_distribution():
    path = BACKTEST_DIR / "score_distribution.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


@app.get("/api/backtest/trades/{pair}")
def backtest_trades(pair: str):
    safe = pair.replace("=", "_").replace("^", "")
    path = BACKTEST_DIR / f"trades_{safe}_1h.csv"
    if not path.exists():
        path = BACKTEST_DIR / f"trades_{pair}_1h.csv"
    if not path.exists():
        raise HTTPException(404, f"No trades for {pair}")
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/api/engine-log")
def engine_log(lines: int = 200):
    text = _tail_text(ENGINE_LOG)
    return {"lines": text.splitlines()[-lines:], "path": str(ENGINE_LOG)}


@app.get("/api/orders")
def orders(limit: int = 100):
    return _read_jsonl(ORDERS_LOG, limit=limit)


@app.get("/api/kill-switch")
def get_kill_switch():
    return {"enabled": KILL_SWITCH.exists()}


@app.post("/api/kill-switch")
def set_kill_switch(body: KillSwitchBody):
    KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
    if body.enabled:
        KILL_SWITCH.write_text("1")
    else:
        KILL_SWITCH.unlink(missing_ok=True)
    return {"enabled": body.enabled}


def _run_scan(
    *,
    pair: Optional[str] = None,
    timeframe: str = "1h",
    mode: str = "replay",
    period: str = "5d",
    refresh_bias: bool = True,
    reason: str = "manual",
) -> dict:
    """Run the signal engine once. Writes to engine.log so the Log tab and
    live tape update; optionally refreshes multi-TF / COT bias and retrains.

    Shared by the manual POST /api/scan endpoint and the recurring
    auto-scan background loop (AUTO_SCAN_ENABLED) so both paths feed the
    engines through the exact same, well-tested code path.
    """
    mode = (mode or "replay").lower()
    if mode not in ("replay", "live"):
        mode = "replay"
    ENGINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "delivery" / "runner.py"),
        "--mode",
        mode,
        "--timeframe",
        timeframe or "1h",
        "--period",
        period or "5d",
        "--alert",
    ]
    if pair:
        cmd.extend(["--pair", pair])
    else:
        cmd.append("--all-pairs")

    header = (
        f"\n===== SCAN {datetime.now(timezone.utc).isoformat()} "
        f"mode={mode} pair={pair or 'ALL'} tf={timeframe} reason={reason} =====\n"
    )
    try:
        with open(ENGINE_LOG, "a", encoding="utf-8") as logf:
            logf.write(header)
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        out = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        with open(ENGINE_LOG, "a", encoding="utf-8") as logf:
            logf.write(out)
            logf.write(
                f"\n===== SCAN DONE rc={result.returncode} =====\n"
            )
        bias_rows = []
        bias_error = None
        if refresh_bias and refresh_pair_bias is not None:
            try:
                pairs = [pair] if pair else list(WATCH_LIST)
                bias_rows = refresh_pair_bias(pairs=pairs, timeframe=timeframe or "1h")
            except Exception as e:
                bias_error = str(e)
        retrain_result = None
        if result.returncode == 0 and get_learner:
            try:
                from engine.ml.learner import auto_retrain
                # Replay simulates win/loss on fired bars → new labels; retrain if enough new
                retrain_result = auto_retrain(reason="scan")
            except Exception as e:
                retrain_result = {"ok": False, "reason": str(e)}

        # Pull the runner's machine-readable summary (fired count, which
        # pairs it attempted, and — critically — which pairs' live fetch
        # failed) out of stdout. A subprocess that exits 0 only tells us it
        # didn't crash; it says nothing about whether it actually got fresh
        # market data. Without this, "yfinance rate-limited / unreachable
        # for GBPJPY" looked identical to "GBPJPY had nothing to report" —
        # the scan reported ok=true either way, so a data outage for one or
        # more pairs was invisible and their last signal just quietly went
        # stale relative to the pairs that kept fetching fine.
        fired_count = None
        pairs_attempted: list[str] = []
        fetch_errors: list[dict] = []
        for line in (result.stdout or "").splitlines():
            if line.startswith("RUNNER_SUMMARY:"):
                try:
                    summary = json.loads(line[len("RUNNER_SUMMARY:"):])
                    fired_count = summary.get("fired")
                    pairs_attempted = summary.get("pairs_attempted") or []
                    fetch_errors = summary.get("fetch_errors") or []
                except Exception:
                    pass
                break

        failed_pairs = sorted({e.get("pair") for e in fetch_errors if e.get("pair")})
        attempted_set = {p for p in pairs_attempted}
        data_ok = result.returncode == 0 and not fetch_errors
        warning = None
        if fetch_errors:
            if attempted_set and failed_pairs and set(failed_pairs) >= attempted_set:
                warning = (
                    f"Live data fetch failed for every pair attempted this scan "
                    f"({', '.join(failed_pairs)}) — no fresh candles, so no signals "
                    f"could fire. This scan did not update any pair's data. "
                    f"Check network/yfinance access (see DOCKER.md: yfinance rate limits)."
                )
            else:
                warning = (
                    f"Live data fetch failed for: {', '.join(failed_pairs)} — those "
                    f"pairs were not refreshed this scan and their last signal levels "
                    f"are now stale relative to the pairs that did update."
                )

        return {
            "ok": result.returncode == 0,
            # True only when the scan both ran AND actually got fresh data
            # for every pair it attempted. `ok` alone can't tell the two
            # apart — see the comment above.
            "data_ok": data_ok,
            "mode": mode,
            "fired": fired_count,
            "pairs_attempted": pairs_attempted,
            "fetch_errors": fetch_errors,
            "warning": warning,
            "stdout": (result.stdout or "")[-3000:],
            "stderr": (result.stderr or "")[-1500:],
            "bias_refreshed": bool(bias_rows),
            "bias_count": len(bias_rows),
            "bias_error": bias_error,
            "retrain": retrain_result,
        }
    except subprocess.TimeoutExpired:
        with open(ENGINE_LOG, "a", encoding="utf-8") as logf:
            logf.write("\n===== SCAN TIMEOUT =====\n")
        return {"ok": False, "error": "timeout", "detail": "Scan timed out (180s)"}
    except Exception as e:
        return {"ok": False, "error": "exception", "detail": str(e)}


@app.post("/api/scan")
def scan_now(body: ScanBody = ScanBody()):
    """Run the signal engine on demand from the front end (manual trigger).
    Delegates to `_run_scan`, the same function the recurring auto-scan
    background loop uses, so manual and automatic runs behave identically.

    Never gated on ML health — scans are the only source of new closed
    outcomes, so blocking them on an overfit signal is self-defeating.
    """
    result = _run_scan(
        pair=body.pair,
        timeframe=body.timeframe,
        mode=body.mode,
        period=body.period,
        refresh_bias=body.refresh_bias,
        reason="manual",
    )
    if result.get("error") == "timeout":
        raise HTTPException(504, result.get("detail", "Scan timed out"))
    if result.get("error") == "exception":
        raise HTTPException(500, result.get("detail", "Scan failed"))
    return result


@app.patch("/api/signals/{signal_id}/outcome")
def set_outcome(signal_id: int, body: OutcomeBody):
    if body.outcome not in ("win", "loss", "pending"):
        raise HTTPException(400, "invalid outcome")
    update_outcome(signal_id, body.outcome, auto_retrain=False)
    retrain_result = None
    if get_learner and body.outcome in ("win", "loss"):
        try:
            from engine.ml.learner import auto_retrain
            # force=False → respects min_new_labels + cooldown; first labels still train when pool ≥ min
            retrain_result = auto_retrain(reason=f"outcome:{signal_id}:{body.outcome}")
            if retrain_result.get("ok"):
                line = (
                    f"[ml] auto-retrain ok n={retrain_result.get('n_samples')} "
                    f"acc={retrain_result.get('train_accuracy')} "
                    f"trigger=outcome:{signal_id}"
                )
                try:
                    ENGINE_LOG.parent.mkdir(parents=True, exist_ok=True)
                    with open(ENGINE_LOG, "a", encoding="utf-8") as logf:
                        logf.write(line + "\n")
                except Exception:
                    pass
        except Exception as e:
            retrain_result = {"ok": False, "reason": str(e)}
    return {"id": signal_id, "outcome": body.outcome, "retrain": retrain_result}


@app.post("/api/signals/{signal_id}/second-opinion")
def post_second_opinion(signal_id: int, body: SecondOpinionBody):
    """Accept a sister-system (Sentinel) Confirm/Caution/Veto for a signal.

    Stored on the signal row and surfaced via /api/signals enrichment so the
    React desk can show the verdict inline.
    """
    verdict = (body.final_verdict or "").upper().strip()
    if verdict not in ("CONFIRM", "CAUTION", "VETO"):
        raise HTTPException(400, "final_verdict must be CONFIRM, CAUTION, or VETO")
    opinion = body.model_dump()
    opinion["final_verdict"] = verdict
    from datetime import datetime, timezone
    opinion["received_at"] = datetime.now(timezone.utc).isoformat()
    ok = update_second_opinion(signal_id, opinion)
    if not ok:
        raise HTTPException(404, f"signal id {signal_id} not found")
    return {"ok": True, "id": signal_id, "second_opinion": opinion}


@app.get("/api/signals/{signal_id}/second-opinion")
def get_second_opinion(signal_id: int):
    rows = get_signals(limit=10_000)
    for r in rows:
        if int(r.get("id", -1)) == int(signal_id):
            so = r.get("second_opinion")
            if not so:
                raise HTTPException(404, "no second opinion recorded")
            return so
    raise HTTPException(404, f"signal id {signal_id} not found")


@app.get("/api/pair-bias")
def get_pair_bias():
    """Cached multi-TF / structure / zone / COT divergence snapshot."""
    rows = load_pair_bias() if load_pair_bias else []
    return {"rows": rows, "count": len(rows)}


@app.post("/api/pair-bias/refresh")
def post_pair_bias_refresh(timeframe: str = "1h"):
    if refresh_pair_bias is None:
        raise HTTPException(503, "pair_bias module unavailable")
    rows = refresh_pair_bias(timeframe=timeframe)
    return {"ok": True, "count": len(rows), "rows": rows}


@app.get("/api/glossary")
def glossary():
    try:
        md = glossary_markdown()
    except Exception as e:
        return {"markdown": f"### Glossary\n\n_(unavailable: {e})_"}
    return {"markdown": md or "### Glossary\n\n_(empty)_"}


@app.get("/api/ml/status")
def ml_status():
    if not get_learner:
        return {"available": False}
    raw = get_learner().status()

    # Flatten to match the frontend's MlStatus contract (see
    # HANDOVER_BLANK_SCREEN_BUG.md): the frontend expects `health` to be a
    # plain string, not the nested object the learner produces internally.
    health = raw.get("health") or {}
    walk_forward = raw.get("walk_forward") or {}

    return {
        **raw,
        "health": health.get("state", "unknown") if isinstance(health, dict) else health,
        "health_detail": health if isinstance(health, dict) else None,
        "n_samples": raw.get("n_train"),
        "train_acc": raw.get("train_accuracy"),
        "walk_forward": walk_forward.get("accuracy") if isinstance(walk_forward, dict) else walk_forward,
        "walk_forward_detail": walk_forward if isinstance(walk_forward, dict) else None,
        "last_train": raw.get("last_fit"),
        "paused": health.get("learning_paused") if isinstance(health, dict) else None,
        "explore": health.get("explore_mode") if isinstance(health, dict) else None,
    }


@app.post("/api/ml/retrain")
def ml_retrain():
    if not get_learner:
        raise HTTPException(503, "ML not available")
    # Manual button always forces a fit attempt
    try:
        from engine.ml.learner import auto_retrain
        return auto_retrain(reason="manual", force=True)
    except Exception:
        return get_learner().fit_from_logs()


@app.get("/api/ml/insights")
def ml_insights():
    if not get_learner:
        return {"insights": [], "available": False}
    return {"insights": get_learner().insights(), "available": True}



_TF_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _max_candle_age_sec(timeframe: str) -> float:
    """How old the newest cached candle may be before we refetch.

    One bar plus a 25% grace. FX is closed at weekends, so we allow up to
    ~3 days on top — otherwise every Sunday request would hammer yfinance for
    data that genuinely does not exist yet.
    """
    bar = _TF_SECONDS.get(timeframe, 3600)
    return bar * 1.25 + 259_200


def _candle_age_sec(df) -> Optional[float]:
    """Seconds between now (UTC) and the open time of the newest row."""
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return None
    try:
        last = df.index[-1]
        if not isinstance(last, pd.Timestamp):
            last = pd.Timestamp(last)
        last = last.tz_localize("UTC") if last.tzinfo is None else last.tz_convert("UTC")
        return float((pd.Timestamp.now(tz="UTC") - last).total_seconds())
    except Exception:
        return None


@app.get("/api/candles/{pair}")
def get_candles(
    pair: str,
    timeframe: str = Query("1h", description="15m | 1h | 4h | 1d"),
    limit: int = Query(120, ge=20, le=500),
    period: str = Query("60d"),
):
    """OHLC candles for charting.

    Cache policy: the on-disk CSV is only trusted while it is *fresh*. Anything
    older than one bar of the requested timeframe is refetched from yfinance.
    (The previous version returned the cache whenever the file merely existed,
    so on a deployment with a persistent disk the chart froze at whatever was
    fetched the very first time.) A stale cache is still used as a fallback if
    the refetch fails, but the response says so.
    """
    import pandas as pd
    from data.candles import load_cached, fetch_history

    # Normalize pair (allow EURUSD or EURUSD=X)
    raw = pair
    if pair.upper() in ("US30", "DJI"):
        pair = "^DJI"
    elif "=" not in pair and not pair.startswith("^"):
        pair = f"{pair}=X" if not pair.endswith("=X") else pair

    df = None
    source = "cache"
    stale = False

    cached = None
    cached_age = None
    try:
        cached = load_cached(pair, timeframe)
        cached_age = _candle_age_sec(cached)
    except Exception:
        cached = None

    fresh_enough = (
        cached is not None
        and not cached.empty
        and cached_age is not None
        and cached_age <= _max_candle_age_sec(timeframe)
    )

    if fresh_enough:
        df = cached
        source = "cache"
    else:
        try:
            df = fetch_history(pair, timeframe, period=period)
            source = "yfinance"
        except Exception as e:
            if cached is not None and not cached.empty:
                # Network/provider failed — serve the stale cache but flag it
                # rather than silently pretending it is current.
                df = cached
                source = "cache-stale"
                stale = True
            else:
                # last resort: synthetic from data/_synthetic if available
                try:
                    from data._synthetic import generate_synthetic_candles
                    from data.instruments import get_instrument
                    try:
                        inst = get_instrument(pair)
                        start = float(inst.typical_price)
                    except Exception:
                        start = 1.085 if "JPY" not in pair.upper() else 150.0
                        if "DJI" in pair.upper() or "US30" in pair.upper():
                            start = 38000.0
                    freq = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}.get(timeframe, "1h")
                    df = generate_synthetic_candles(n=max(limit, 200), start_price=start, freq=freq)
                    source = "synthetic"
                    stale = True
                except Exception:
                    raise HTTPException(503, f"No candle data for {raw} @ {timeframe}: {e}")

    if df is None or df.empty:
        raise HTTPException(404, f"Empty candle series for {pair} @ {timeframe}")

    # Ensure columns
    cols = {c.lower(): c for c in df.columns}
    def col(name):
        for k, v in cols.items():
            if k == name.lower():
                return v
        return name

    df = df.tail(limit).copy()
    # reset index for JSON
    if df.index.name or isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index()
    time_col = None
    for candidate in ("Datetime", "datetime", "Date", "date", "index"):
        if candidate in df.columns:
            time_col = candidate
            break
    if time_col is None:
        df["Datetime"] = pd.RangeIndex(len(df))
        time_col = "Datetime"

    rows = []
    for _, r in df.iterrows():
        ts = r[time_col]
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()
        else:
            ts = str(ts)
        try:
            o = float(r[col("Open")])
            h = float(r[col("High")])
            l = float(r[col("Low")])
            c = float(r[col("Close")])
        except Exception:
            continue
        vol = 0.0
        try:
            vol = float(r[col("Volume")])
        except Exception:
            pass
        rows.append({"t": ts, "o": o, "h": h, "l": l, "c": c, "v": vol})

    age_sec = _candle_age_sec(df.set_index(time_col)) if time_col in df.columns else None

    return {
        "pair": pair,
        "timeframe": timeframe,
        "source": source,
        "stale": bool(stale),
        # Age of the newest bar, in seconds. The UI uses this to show how old
        # the chart really is instead of implying everything is live.
        "age_sec": round(age_sec) if age_sec is not None else None,
        "as_of": rows[-1]["t"] if rows else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "candles": rows,
    }


@app.get("/api/candles")
def get_candles_query(
    pair: str = Query(...),
    timeframe: str = Query("1h"),
    limit: int = Query(120, ge=20, le=500),
    period: str = Query("60d"),
):
    return get_candles(pair=pair, timeframe=timeframe, limit=limit, period=period)


# --- Serve React SPA (production build in frontend/dist) ---
if DIST.exists() and (DIST / "index.html").exists():
    assets_dir = DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def spa_index():
        return FileResponse(DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # Do not swallow API / WS
        if full_path.startswith("api") or full_path.startswith("ws"):
            raise HTTPException(404, "Not found")
        candidate = DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)
