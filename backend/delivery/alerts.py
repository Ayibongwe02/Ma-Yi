"""
Alert delivery — Telegram bot (primary) + console fallback.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment
(or .env). When missing, messages are printed so local testing still works.
"""

from __future__ import annotations

import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()


def _telegram_credentials() -> tuple[Optional[str], Optional[str]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or None
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or None
    if token and chat_id:
        return token, chat_id
    return None, None


def format_signal_message(
    pair: str,
    timeframe: str,
    pattern: str,
    direction: int,
    score: float,
    entry: float,
    sl: float,
    tp: float,
    bar_time: str,
    regime: str | None = None,
    sr_score: float | None = None,
    volatility: str | None = None,
    plain: bool = True,
) -> str:
    """Format a signal for Telegram / console.

    plain=True (default) produces a narrative non-traders can read.
    plain=False keeps the compact jargon card for power users.
    """
    if plain:
        try:
            from plain_language import format_plain_signal_message
            return format_plain_signal_message(
                pair, timeframe, pattern, direction, score,
                entry, sl, tp, bar_time,
                regime=regime, sr_score=sr_score, volatility=volatility,
            )
        except Exception:
            pass  # fall through to compact format

    side = "Buy 🟢" if direction > 0 else "Sell 🔴"
    clean_pair = pair.replace("=X", "").replace("=x", "").replace("^", "")
    return (
        f"📡 *Market signal*\n"
        f"Market: `{clean_pair}`  ({timeframe})\n"
        f"Direction: *{side}*\n"
        f"Pattern: `{pattern}`\n"
        f"Score: *{score:.1f}*\n"
        f"Entry: `{entry}`\n"
        f"Max loss: `{sl}`\n"
        f"Target: `{tp}`\n"
        f"Bar: `{bar_time}`"
    )


def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message. Returns True on success."""
    token, chat_id = _telegram_credentials()
    if not token or not chat_id:
        print("[alert:console] Telegram credentials not set — message follows:")
        print(text)
        print("-" * 40)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        print(f"[alert] Telegram API error: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        print(f"[alert] Telegram send failed: {e}")
        return False


def send_signal_alert(
    pair: str,
    timeframe: str,
    pattern: str,
    direction: int,
    score: float,
    entry: float,
    sl: float,
    tp: float,
    bar_time: str,
    regime: str | None = None,
    sr_score: float | None = None,
    volatility: str | None = None,
) -> bool:
    msg = format_signal_message(
        pair, timeframe, pattern, direction, score, entry, sl, tp, bar_time,
        regime=regime, sr_score=sr_score, volatility=volatility,
    )
    return send_telegram(msg)
