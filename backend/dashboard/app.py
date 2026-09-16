"""
Ma-yi Live Command Center — Streamlit web desk.

Tabs:
  - Live Command : auto-scanning watch list, Act now / Watch triage, bias, R-curve
  - Signals      : plain-language cards (entry / max-loss / target)
  - Backtest     : win rates, expectancy, per-pattern reports
  - Glossary     : pip, stop, target, confidence
  - Engine log   : scan and signal tape
  - Execution    : kill switch, score/daily caps, dry-run blotter (no live broker)

Controls: pause/resume scan refresh, change interval, Scan now, queue dry-run
from an open card. Nothing places a real order unless EXECUTE_ENABLED + live-broker
are both intentionally set outside this UI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "delivery"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "execution"))

from logger import get_signals, stats, init_db, get_log_path
from plain_language import (
    confidence_label,
    display_pair,
    glossary_markdown,
    why_this_fired,
    scale_for,
    distance_in_pips,
)

st.set_page_config(
    page_title="Ma-yi Live Command",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()

# ── paths & constants ──────────────────────────────────────────────────────
ENGINE_LOG_PATH = ROOT / "delivery" / "logs" / "engine.log"
ORDERS_LOG_PATH = ROOT / "execution" / "logs" / "orders.jsonl"
KILL_SWITCH_FILE = ROOT / "execution" / "logs" / "kill_switch.flag"
BACKTEST_DIR = ROOT / "backtest" / "reports"
TAIL_READ_BYTES = 100_000
MAX_LOG_LINES = 400

SCORE_ACT_NOW = 70.0
SCORE_WATCH = 50.0
SCORE_NOISE = 40.0
RECENCY_HOURS_ACT = 6
RECENCY_HOURS_WATCH = 24

WATCH_LIST = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "GBPJPY=X", "^DJI"]
WATCH_LABELS = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "GBPJPY=X": "GBP/JPY",
    "^DJI": "US30",
}


# ── helpers ────────────────────────────────────────────────────────────────
def _meta_get(row: Any, key: str, default=None):
    meta = row.get("meta") if hasattr(row, "get") else None
    if isinstance(meta, dict):
        return meta.get(key, default)
    return default


def _parse_dt(val) -> datetime | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        ts = pd.to_datetime(val, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def _age_hours(bar_time) -> float | None:
    dt = _parse_dt(bar_time)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def triage_signal(row: dict | pd.Series) -> str:
    score = abs(float(row.get("final_score") or 0))
    outcome = str(row.get("outcome") or "pending").lower()
    fired = bool(row.get("fired", True))
    age = _age_hours(row.get("bar_time"))

    if not fired or score < SCORE_NOISE:
        return "noise"
    if outcome in ("win", "loss"):
        return "noise" if age is not None and age > RECENCY_HOURS_WATCH else "watch"

    if score >= SCORE_ACT_NOW and (age is None or age <= RECENCY_HOURS_ACT):
        return "act_now"
    if score >= SCORE_WATCH and (age is None or age <= RECENCY_HOURS_WATCH):
        return "watch"
    return "noise"


def priority_badge(bucket: str) -> str:
    return {
        "act_now": "🔴 ACT NOW",
        "watch": "🟡 WATCH",
        "noise": "⚪ NOISE",
    }.get(bucket, "⚪")


def direction_emoji(direction: int) -> str:
    if direction > 0:
        return "🟢 BUY"
    if direction < 0:
        return "🔴 SELL"
    return "⚪ —"


def confidence_color(label: str) -> str:
    return {
        "Strong": "#16a34a",
        "Moderate": "#ca8a04",
        "Weak": "#6b7280",
    }.get(label, "#6b7280")


def is_kill_switch_on() -> bool:
    if KILL_SWITCH_FILE.exists():
        return True
    return os.environ.get("EXEC_KILL_SWITCH", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def set_kill_switch(on: bool) -> None:
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    if on:
        KILL_SWITCH_FILE.write_text("1\n")
    else:
        if KILL_SWITCH_FILE.exists():
            KILL_SWITCH_FILE.unlink()


def load_orders(limit: int = 100) -> list[dict]:
    if not ORDERS_LOG_PATH.exists():
        return []
    rows = []
    try:
        with ORDERS_LOG_PATH.open("r", encoding="utf-8") as f:
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
    return rows[-limit:]


def render_signal_card(
    row: pd.Series | dict,
    bucket: str,
    compact: bool = False,
    allow_queue: bool = False,
) -> None:
    direction = int(row.get("direction") or 0)
    score = float(row.get("final_score") or 0)
    pattern = str(row.get("pattern") or "")
    pair = str(row.get("pair") or "")
    regime = _meta_get(row, "regime") or ""
    sr = float(row.get("sr_score") or 0)
    vol = str(row.get("volatility") or "normal")
    side = "Buy opportunity" if direction > 0 else "Sell opportunity"
    label = confidence_label(score)
    name = display_pair(pair)
    why = why_this_fired(pattern, direction, regime, sr, vol)
    scale = scale_for(pair)
    pip_size = float(scale.get("pip_size") or 0.0001)
    unit = "points" if pip_size >= 1.0 else "pips"
    entry = row.get("entry")
    sl = row.get("sl")
    tp = row.get("tp")
    outcome = str(row.get("outcome") or "—")
    outcome_plain = {
        "win": "✅ Hit target",
        "loss": "❌ Hit max-loss",
        "pending": "⏳ Still open",
        "n/a": "—",
    }.get(outcome, outcome)
    age = _age_hours(row.get("bar_time"))
    age_txt = f"{age:.1f}h ago" if age is not None else "—"
    badge = priority_badge(bucket)

    border_color = {
        "act_now": "#dc2626",
        "watch": "#ca8a04",
        "noise": "#9ca3af",
    }.get(bucket, "#9ca3af")

    with st.container(border=True):
        h1, h2, h3, h4 = st.columns([2.5, 1.2, 1.2, 1.1])
        h1.markdown(
            f"**{badge}** · {direction_emoji(direction)} · **{name}** · {row.get('timeframe', '')}"
        )
        h2.markdown(f"**{label}** · score `{abs(score):.0f}`")
        h3.markdown(f"{outcome_plain}")
        h4.caption(f"Bar {age_txt}")

        if not compact:
            st.write(why)
            if entry is not None and sl is not None and tp is not None:
                risk_u = distance_in_pips(float(entry), float(sl), pip_size)
                reward_u = distance_in_pips(float(entry), float(tp), pip_size)
                rr = reward_u / risk_u if risk_u > 0 else 0
                st.caption(
                    f"Enter near **{entry}** · Max-loss **{sl}** (~{risk_u:.1f} {unit}) · "
                    f"Target **{tp}** (~{reward_u:.1f} {unit}, ~{rr:.1f}R) · "
                    f"`{row.get('bar_time', '')}`"
                )

        if allow_queue and outcome == "pending" and bucket in ("act_now", "watch"):
            sig_id = row.get("id") or f"{pair}-{row.get('bar_time')}"
            if st.button(
                "Queue dry-run",
                key=f"queue_{sig_id}_{bucket}",
                help="Write a dry-run order record only — never hits a broker",
            ):
                queue_dry_run(row)
                st.success("Dry-run queued → see Execution tab blotter")


def queue_dry_run(row: pd.Series | dict) -> None:
    """Append a dry-run order record. Never contacts a broker."""
    ORDERS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    order = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run",
        "source": "dashboard-queue",
        "pair": str(row.get("pair") or ""),
        "direction": int(row.get("direction") or 0),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tp": row.get("tp"),
        "final_score": float(row.get("final_score") or 0),
        "pattern": str(row.get("pattern") or ""),
        "timeframe": str(row.get("timeframe") or ""),
        "status": "queued-dry",
        "note": "Queued from Live Command card. No broker call.",
    }
    with ORDERS_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(order, default=str) + "\n")


def build_auto_analysis(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "n_act": 0,
            "n_watch": 0,
            "n_noise": 0,
            "n_pending": 0,
            "bias": "No data",
            "bias_detail": "Run the engine to generate signals.",
            "top_pairs": [],
            "strong_pending": [],
            "avg_score_pending": 0.0,
        }

    buckets = df.apply(triage_signal, axis=1)
    n_act = int((buckets == "act_now").sum())
    n_watch = int((buckets == "watch").sum())
    n_noise = int((buckets == "noise").sum())

    pending = df[df["outcome"].astype(str).str.lower() == "pending"]
    n_pending = len(pending)

    recent = df.copy()
    recent["_age"] = recent["bar_time"].apply(_age_hours)
    recent = recent[(recent["_age"].isna()) | (recent["_age"] <= RECENCY_HOURS_WATCH)]
    if len(recent) == 0:
        bias = "Neutral / quiet"
        bias_detail = "No recent signals in the watch window."
    else:
        buy_w = recent[recent["direction"] > 0]["final_score"].abs().sum()
        sell_w = recent[recent["direction"] < 0]["final_score"].abs().sum()
        total = buy_w + sell_w
        if total < 1:
            bias = "Neutral / quiet"
            bias_detail = "Scores too low to lean either way."
        elif buy_w > sell_w * 1.25:
            bias = "Bullish lean"
            bias_detail = (
                f"Buy-side weight {buy_w:.0f} vs sell {sell_w:.0f} "
                f"in last {RECENCY_HOURS_WATCH}h."
            )
        elif sell_w > buy_w * 1.25:
            bias = "Bearish lean"
            bias_detail = (
                f"Sell-side weight {sell_w:.0f} vs buy {buy_w:.0f} "
                f"in last {RECENCY_HOURS_WATCH}h."
            )
        else:
            bias = "Mixed / balanced"
            bias_detail = f"Buy {buy_w:.0f} ≈ sell {sell_w:.0f} — no clear lean."

    pair_scores = (
        df.assign(abs_score=df["final_score"].abs())
        .groupby("pair")["abs_score"]
        .max()
        .sort_values(ascending=False)
        .head(5)
    )
    top_pairs = [(display_pair(p), float(s)) for p, s in pair_scores.items()]

    strong_pending = pending[pending["final_score"].abs() >= SCORE_ACT_NOW].sort_values(
        "final_score", key=abs, ascending=False
    )
    strong_list = []
    for _, r in strong_pending.head(5).iterrows():
        strong_list.append(
            {
                "pair": display_pair(str(r["pair"])),
                "side": "BUY" if int(r.get("direction") or 0) > 0 else "SELL",
                "score": abs(float(r.get("final_score") or 0)),
                "tf": r.get("timeframe", ""),
            }
        )

    avg_score = float(pending["final_score"].abs().mean()) if n_pending else 0.0

    return {
        "n_act": n_act,
        "n_watch": n_watch,
        "n_noise": n_noise,
        "n_pending": n_pending,
        "bias": bias,
        "bias_detail": bias_detail,
        "top_pairs": top_pairs,
        "strong_pending": strong_list,
        "avg_score_pending": avg_score,
    }


def tail_log(path: Path, max_lines: int = MAX_LOG_LINES) -> str:
    if not path.exists():
        return (
            "No engine.log yet.\n\n"
            "This tab tails delivery/logs/engine.log, written when the signal "
            "engine runs in the background next to this dashboard.\n\n"
            "Run `python delivery/runner.py --mode live` (or replay) to generate signals.\n"
            "Or use **Scan now** in the sidebar for a one-shot pass."
        )
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > TAIL_READ_BYTES:
                f.seek(size - TAIL_READ_BYTES)
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            return "(engine.log is empty — the engine hasn't printed anything yet)"
        return "\n".join(lines[-max_lines:])
    except OSError as e:
        return f"(couldn't read engine.log: {e})"


def run_one_shot_scan() -> str:
    """Trigger a single pass over the default watch list (live mode uses PAIRS from env)."""
    try:
        # live mode loads PAIRS from env / config; one pass per pair+tf
        cmd = [
            sys.executable,
            str(ROOT / "delivery" / "runner.py"),
            "--mode",
            "live",
            "--timeframe",
            "1h",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        out = (result.stdout or "") + (result.stderr or "")
        return out[-4000:] if out else f"Exit code {result.returncode} (no stdout)"
    except subprocess.TimeoutExpired:
        return "Scan timed out after 180s — check engine.log"
    except Exception as e:
        return f"Scan failed: {e}"


# ── Session state defaults ─────────────────────────────────────────────────
if "paused" not in st.session_state:
    st.session_state.paused = False
if "last_scan_msg" not in st.session_state:
    st.session_state.last_scan_msg = ""


# ── Sidebar: global controls ───────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Live controls")
    st.caption("Auto-refresh uses Streamlit fragments (WebSocket-backed).")

    # Pause / resume
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        if st.button("⏸ Pause" if not st.session_state.paused else "▶ Resume"):
            st.session_state.paused = not st.session_state.paused
            st.rerun()
    with col_p2:
        if st.button("🔄 Scan now"):
            with st.spinner("Running one-shot scan over watch list…"):
                msg = run_one_shot_scan()
                st.session_state.last_scan_msg = msg
            st.rerun()

    if st.session_state.paused:
        st.warning("Auto-refresh **paused**")
    else:
        st.success("Auto-refresh **live**")

    refresh_sec = st.select_slider(
        "Auto-refresh interval (seconds)",
        options=[0, 3, 5, 10, 15, 30, 60],
        value=10,
        help=(
            "Recommended: **10 s** for live awareness, **5 s** when actively "
            "managing entries, **30–60 s** for quieter monitoring. 0 = manual only."
        ),
        disabled=st.session_state.paused,
    )
    run_every = None if st.session_state.paused or refresh_sec == 0 else refresh_sec

    st.divider()
    st.subheader("Watch list")
    st.markdown(
        " · ".join(WATCH_LABELS.get(p, p) for p in WATCH_LIST)
    )

    st.divider()
    st.subheader("Signal filters")
    all_rows = get_signals(limit=5000)
    df_all = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    pair_sel = "All"
    fired_only = True
    min_conf = "Any"
    show_noise = False
    date_range = None
    show_advanced = False

    if not all_rows:
        st.warning("No signals logged yet. Hit **Scan now** or run the engine.")
    else:
        pairs = sorted(df_all["pair"].dropna().unique().tolist())
        pair_sel = st.selectbox("Market", ["All"] + pairs)
        fired_only = st.checkbox("Fired signals only", value=True)
        min_conf = st.selectbox(
            "Min confidence",
            ["Any", "Moderate+", "Strong only"],
            index=0,
        )
        show_noise = st.checkbox("Show noise bucket", value=False)
        show_advanced = st.checkbox("Show advanced (raw) table", value=False)

        if "bar_time" in df_all.columns:
            df_all["bar_dt"] = pd.to_datetime(
                df_all["bar_time"], errors="coerce", utc=True
            )
            min_d, max_d = df_all["bar_dt"].min(), df_all["bar_dt"].max()
            if pd.notna(min_d) and pd.notna(max_d):
                date_range = st.date_input(
                    "Date range",
                    value=(min_d.date(), max_d.date()),
                    min_value=min_d.date(),
                    max_value=max_d.date(),
                )

    st.divider()
    kill_on = is_kill_switch_on()
    st.subheader("⚡ Kill switch")
    new_kill = st.toggle("Block all new orders", value=kill_on)
    if new_kill != kill_on:
        set_kill_switch(new_kill)
        st.rerun()
    if new_kill:
        st.error("KILL SWITCH ON — no new orders")
    else:
        st.caption("Kill switch off (normal)")

    st.divider()
    st.caption(
        f"Log: `{get_log_path()}`\n\n"
        "Triage rules:\n"
        f"• **Act now** — |score| ≥ {SCORE_ACT_NOW:.0f}, pending, ≤{RECENCY_HOURS_ACT}h\n"
        f"• **Watch** — |score| ≥ {SCORE_WATCH:.0f} or recent closed\n"
        f"• **Noise** — weak, old, or already resolved"
    )

    if st.session_state.last_scan_msg:
        with st.expander("Last Scan now output", expanded=False):
            st.code(st.session_state.last_scan_msg, language="text")


# ── Header ─────────────────────────────────────────────────────────────────
st.title("📡 Ma-yi Live Command Center")
st.caption(
    "Auto-scanning watch list · priority triage · plain-language signals · "
    "backtest reports · dry-run execution. Not financial advice."
)

tab_cmd, tab_signals, tab_backtest, tab_glossary, tab_logs, tab_exec = st.tabs(
    [
        "🎯 Live Command",
        "📋 Signals",
        "📊 Backtest",
        "📖 Glossary",
        "📜 Engine log",
        "⚙️ Execution",
    ]
)


def _load_filtered() -> pd.DataFrame:
    pair_arg = None if pair_sel == "All" else pair_sel
    rows = get_signals(pair=pair_arg, fired_only=fired_only, limit=2000)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["bar_dt"] = pd.to_datetime(df["bar_time"], errors="coerce", utc=True)
    if date_range and len(date_range) == 2:
        start = pd.Timestamp(date_range[0], tz="UTC")
        end = pd.Timestamp(date_range[1], tz="UTC") + pd.Timedelta(days=1)
        df = df[(df["bar_dt"] >= start) & (df["bar_dt"] < end)]
    if min_conf == "Moderate+":
        df = df[df["final_score"].abs() >= SCORE_WATCH]
    elif min_conf == "Strong only":
        df = df[df["final_score"].abs() >= SCORE_ACT_NOW]
    return df


# ── Tab 1: Live Command ────────────────────────────────────────────────────
with tab_cmd:

    @st.fragment(run_every=run_every)
    def _command_centre() -> None:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        status = "paused" if st.session_state.paused else f"interval {refresh_sec or 'manual'}s"
        st.caption(f"Last refresh: **{now_str}** · {status}")

        # Watch-list strip
        st.markdown(
            "**Watch list:** "
            + " · ".join(f"`{WATCH_LABELS.get(p, p)}`" for p in WATCH_LIST)
        )

        df = _load_filtered()
        if df.empty:
            st.info(
                "Nothing to show yet. Hit **Scan now** in the sidebar, or start the engine:\n\n"
                "`python delivery/runner.py --mode live`  or  `--mode replay`"
            )
            return

        analysis = build_auto_analysis(df)

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("🔴 Act now", analysis["n_act"])
        k2.metric("🟡 Watch", analysis["n_watch"])
        k3.metric("⏳ Open", analysis["n_pending"])
        k4.metric("Avg |score| open", f"{analysis['avg_score_pending']:.0f}")
        k5.metric("Bias", analysis["bias"])

        st.caption(analysis["bias_detail"])

        if analysis["strong_pending"]:
            st.subheader("⚡ Highest-priority open setups")
            cols = st.columns(min(3, len(analysis["strong_pending"])))
            for i, s in enumerate(analysis["strong_pending"]):
                with cols[i % len(cols)]:
                    st.markdown(
                        f"**{s['side']}** `{s['pair']}` · {s['tf']} · score **{s['score']:.0f}**"
                    )
        else:
            st.info(
                "No Strong-level open setups right now — nothing that needs immediate action."
            )

        st.subheader("Priority triage")
        df = df.copy()
        df["_bucket"] = df.apply(triage_signal, axis=1)
        act_df = df[df["_bucket"] == "act_now"].sort_values("bar_time", ascending=False)
        watch_df = df[df["_bucket"] == "watch"].sort_values("bar_time", ascending=False)
        noise_df = df[df["_bucket"] == "noise"].sort_values("bar_time", ascending=False)

        c_act, c_watch = st.columns(2)
        with c_act:
            st.markdown("### 🔴 Act now")
            st.caption("Strong + recent + still open. Review entry / risk immediately.")
            if act_df.empty:
                st.success("Clear — no immediate actions.")
            else:
                for _, row in act_df.head(8).iterrows():
                    render_signal_card(row, "act_now", allow_queue=True)
        with c_watch:
            st.markdown("### 🟡 Watch")
            st.caption("Moderate+ or recently resolved. Stay aware; no forced action.")
            if watch_df.empty:
                st.info("Nothing on the watch list.")
            else:
                for _, row in watch_df.head(8).iterrows():
                    render_signal_card(row, "watch", compact=True, allow_queue=True)

        if show_noise:
            st.markdown("### ⚪ Noise (safe to ignore)")
            if noise_df.empty:
                st.caption("No noise rows under current filters.")
            else:
                for _, row in noise_df.head(6).iterrows():
                    render_signal_card(row, "noise", compact=True)

        st.subheader("Closed-trade R curve")
        closed = df[df["outcome"].isin(["win", "loss"])].sort_values("bar_dt")
        if len(closed) > 0:
            pnl = closed["outcome"].map({"win": 1.0, "loss": -1.0})
            equity = pnl.cumsum()
            chart_df = pd.DataFrame(
                {"running_R": equity.values}, index=closed["bar_dt"].values
            )
            st.line_chart(chart_df, height=200)
            st.caption(
                f"{len(closed)} closed trades · final running R = {equity.iloc[-1]:.1f}"
            )
        else:
            st.caption("No closed trades yet — curve appears after outcomes resolve.")

        if analysis["top_pairs"]:
            st.caption(
                "Top markets by peak |score|: "
                + " · ".join(f"{n} ({s:.0f})" for n, s in analysis["top_pairs"])
            )

    _command_centre()


# ── Tab 2: Signals ─────────────────────────────────────────────────────────
with tab_signals:

    @st.fragment(run_every=run_every)
    def _signals_list() -> None:
        df = _load_filtered()
        if df.empty:
            st.info("No rows match the current filters.")
            return

        st_stats = stats(pair=None if pair_sel == "All" else pair_sel)
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Signals fired", st_stats["total_fired"])
        c2.metric("Wins", st_stats["wins"])
        c3.metric("Losses", st_stats["losses"])
        c4.metric("Still open", st_stats["pending"])
        c5.metric("Win rate", f"{st_stats['win_rate']}%")

        st.subheader("Latest signals (plain language)")
        view_df = df.sort_values("bar_time", ascending=False).head(30)
        for _, row in view_df.iterrows():
            bucket = triage_signal(row)
            if bucket == "noise" and not show_noise:
                continue
            render_signal_card(row, bucket, allow_queue=(bucket != "noise"))

        if show_advanced:
            st.subheader("Advanced view (raw engine fields)")
            display_cols = [
                "bar_time",
                "pair",
                "timeframe",
                "pattern",
                "direction",
                "final_score",
                "entry",
                "sl",
                "tp",
                "outcome",
                "volatility",
                "sr_score",
            ]
            existing = [c for c in display_cols if c in df.columns]
            view = df[existing].copy()
            if "direction" in view.columns:
                view["direction"] = view["direction"].map(
                    {1: "BUY", -1: "SELL", 0: "—"}
                )
            if "final_score" in view.columns:
                view["final_score"] = view["final_score"].round(1)
            if "sr_score" in view.columns:
                view["sr_score"] = view["sr_score"].round(2)
            st.dataframe(
                view.sort_values("bar_time", ascending=False),
                use_container_width=True,
                height=420,
            )

        st.caption(
            "Historical results use a simple forward check (target or max-loss "
            "within 48 bars). Live alerts start as 'still open' until resolved. "
            "This is not financial advice."
        )

    _signals_list()


# ── Tab 3: Backtest ────────────────────────────────────────────────────────
with tab_backtest:
    st.subheader("Backtest reports")
    st.caption(
        "Generated by `python backtest/run_backtest.py`. "
        "Shows win rates, expectancy (R), and per-pattern breakdowns."
    )

    comparison_path = BACKTEST_DIR / "comparison.csv"
    per_pattern_path = BACKTEST_DIR / "per_pattern.csv"
    score_dist_path = BACKTEST_DIR / "score_distribution.json"

    if comparison_path.exists():
        st.markdown("#### Summary by market")
        cmp = pd.read_csv(comparison_path)
        # Friendly display
        show_cols = [
            c
            for c in [
                "pair",
                "timeframe",
                "n_trades",
                "n_closed",
                "wins",
                "losses",
                "win_rate_pct",
                "expectancy_r",
                "total_r",
            ]
            if c in cmp.columns
        ]
        st.dataframe(cmp[show_cols], use_container_width=True, hide_index=True)

        # Quick KPIs
        if "expectancy_r" in cmp.columns and "total_r" in cmp.columns:
            k1, k2, k3 = st.columns(3)
            k1.metric("Best expectancy (R)", f"{cmp['expectancy_r'].max():.3f}")
            k2.metric("Total R (all)", f"{cmp['total_r'].sum():.1f}")
            best = cmp.loc[cmp["expectancy_r"].idxmax()]
            k3.metric(
                "Top market",
                f"{display_pair(str(best['pair']))} ({best['win_rate_pct']:.0f}% WR)",
            )
    else:
        st.info(
            "No comparison.csv yet. Run:\n\n"
            "`python backtest/run_backtest.py`"
        )

    st.divider()

    if per_pattern_path.exists():
        st.markdown("#### Per-pattern performance")
        pp = pd.read_csv(per_pattern_path)
        # Sort by expectancy descending
        if "expectancy_r" in pp.columns:
            pp = pp.sort_values("expectancy_r", ascending=False)
        st.dataframe(pp, use_container_width=True, hide_index=True, height=360)
    else:
        st.caption("per_pattern.csv not found.")

    st.divider()

    # Monte Carlo snippets if present
    mc_files = list(BACKTEST_DIR.glob("montecarlo_*.json"))
    if mc_files:
        st.markdown("#### Monte Carlo (sample)")
        for mc in mc_files[:4]:
            try:
                data = json.loads(mc.read_text())
                st.json(data, expanded=False)
            except Exception:
                st.caption(f"Could not parse {mc.name}")

    if score_dist_path.exists():
        try:
            dist = json.loads(score_dist_path.read_text())
            st.markdown("#### Score distribution (confidence bands)")
            st.json(dist)
        except Exception:
            pass

    # Trade lists expander
    trade_files = list(BACKTEST_DIR.glob("trades_*.csv"))
    if trade_files:
        with st.expander("Raw trade lists"):
            for tf in sorted(trade_files):
                st.markdown(f"**{tf.name}**")
                try:
                    tdf = pd.read_csv(tf)
                    st.dataframe(tdf.head(50), use_container_width=True, hide_index=True)
                except Exception as e:
                    st.caption(str(e))


# ── Tab 4: Glossary ────────────────────────────────────────────────────────
with tab_glossary:
    st.markdown(glossary_markdown())
    st.info(
        "Confidence labels (Weak / Moderate / Strong) are derived from the "
        "distribution of historical signal scores after a backtest run "
        "(`python backtest/run_backtest.py`). Until that report exists, "
        "built-in default bands are used."
    )
    st.markdown(
        """
### How the Live Command triage works
| Bucket | Meaning | Default rule |
|--------|---------|--------------|
| **Act now** | Review and consider acting | |score| ≥ 70, still open, bar ≤ 6 h old |
| **Watch** | Stay aware | |score| ≥ 50 or recently closed |
| **Noise** | Safe to ignore | Weak, old, or already resolved |

Turn **Show noise bucket** on in the sidebar if you want to audit filtered rows.

### Dry-run vs live
Nothing in this dashboard places a real broker order.  
Use **Queue dry-run** on a card (or the runner with `--execute`) to write an order record only.  
Real OANDA orders require `EXECUTE_ENABLED=true` **and** `--live-broker` outside this UI.
"""
    )


# ── Tab 5: Engine log ──────────────────────────────────────────────────────
with tab_logs:
    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.caption(f"Tailing `{ENGINE_LOG_PATH}`")
    with top_r:
        st.caption(f"Refresh every {refresh_sec or '—'}s")

    @st.fragment(run_every=run_every)
    def _render_engine_log() -> None:
        st.code(tail_log(ENGINE_LOG_PATH), language="log")

    _render_engine_log()

    if not run_every:
        if st.button("Refresh log now"):
            st.rerun()


# ── Tab 6: Execution ───────────────────────────────────────────────────────
with tab_exec:
    st.subheader("Execution controls (dry-run by default)")
    st.caption(
        "This panel never places a live order. Kill switch and caps are enforced "
        "by the runner when `--execute` is used. Real broker requires "
        "`EXECUTE_ENABLED=true` + `--live-broker`."
    )

    # Current risk config snapshot
    st.markdown("#### Risk & score caps (from env / defaults)")
    try:
        from risk import RiskConfig

        cfg = RiskConfig.from_env()
        # Override kill from file if present
        cfg.kill_switch = is_kill_switch_on()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Kill switch", "ON ⛔" if cfg.kill_switch else "OFF")
        c2.metric("Min score", f"{cfg.min_score:.0f}")
        c3.metric("Max daily trades", cfg.max_daily_trades)
        c4.metric("Max open positions", cfg.max_open_positions)
        c5, c6, c7 = st.columns(3)
        c5.metric("Risk / trade", f"{cfg.risk_per_trade:.1%}")
        c6.metric("Max units", cfg.max_units)
        c7.metric("Default equity", f"{cfg.default_equity:,.0f}")
    except Exception as e:
        st.warning(f"Could not load RiskConfig: {e}")
        st.markdown(
            f"- Kill switch: **{'ON' if is_kill_switch_on() else 'OFF'}**\n"
            "- Other caps come from `.env` / defaults (see config.example.env)"
        )

    st.divider()
    st.markdown("#### Dry-run blotter")
    st.caption(f"Source: `{ORDERS_LOG_PATH}` (append-only)")

    orders = load_orders(limit=80)
    if not orders:
        st.info(
            "No dry-run orders yet. Queue one from an **Act now** / **Watch** card "
            "on the Live Command tab, or run:\n\n"
            "`python delivery/runner.py --mode replay --execute`"
        )
    else:
        # Newest first
        orders = list(reversed(orders))
        blotter = pd.DataFrame(orders)
        preferred = [
            "ts",
            "mode",
            "status",
            "pair",
            "direction",
            "entry",
            "sl",
            "tp",
            "final_score",
            "pattern",
            "timeframe",
            "note",
            "source",
        ]
        cols = [c for c in preferred if c in blotter.columns]
        # Map direction for readability
        if "direction" in blotter.columns:
            blotter["direction"] = blotter["direction"].map(
                {1: "BUY", -1: "SELL", 0: "—"}
            )
        st.dataframe(
            blotter[cols],
            use_container_width=True,
            hide_index=True,
            height=380,
        )
        st.caption(f"{len(orders)} order records (newest first)")

    st.divider()
    st.markdown("#### Safety notes")
    st.markdown(
        """
- Default is **never** to place real orders.
- Real execution requires `EXECUTE_ENABLED=true` **and** `--live-broker`.
- Prefer `OANDA_ENV=practice` until the system is proven on demo.
- The kill switch (sidebar toggle or `EXEC_KILL_SWITCH`) blocks all new orders.
- This is not financial advice; you are responsible for any live trading.
"""
    )
