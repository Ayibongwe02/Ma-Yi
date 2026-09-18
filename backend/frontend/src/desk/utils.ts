import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { formatDistanceToNow, parseISO, format } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function displayPair(pair: string): string {
  const map: Record<string, string> = {
    "EURUSD=X": "EUR/USD",
    "GBPUSD=X": "GBP/USD",
    "USDJPY=X": "USD/JPY",
    "GBPJPY=X": "GBP/JPY",
    "^DJI": "US30",
  };
  return map[pair] || pair.replace("=X", "").replace("^", "");
}

export function confidenceLabel(score: number): "Strong" | "Moderate" | "Weak" {
  const abs = Math.abs(score);
  if (abs >= 70) return "Strong";
  if (abs >= 50) return "Moderate";
  return "Weak";
}

export function formatAge(barTime?: string | null): string {
  if (!barTime) return "—";
  try {
    return formatDistanceToNow(parseISO(barTime), { addSuffix: true });
  } catch {
    return "—";
  }
}

export function formatTs(ts?: string | null, fmt = "HH:mm:ss"): string {
  if (!ts) return "—";
  try {
    return format(parseISO(ts), fmt);
  } catch {
    return "—";
  }
}

export function priceDigits(pair: string): number {
  if (pair.includes("DJI") || pair.includes("US30")) return 1;
  if (pair.includes("JPY")) return 3;
  return 5;
}

export function formatPrice(price: number, pair: string): string {
  if (!Number.isFinite(price)) return "—";
  return price.toFixed(priceDigits(pair));
}

export function formatPips(risk: number, pair: string): string {
  if (!Number.isFinite(risk)) return "—";
  if (pair.includes("DJI") || pair.includes("US30")) return `${risk.toFixed(1)} pts`;
  const mult = pair.includes("JPY") ? 100 : 10000;
  return `${(Math.abs(risk) * mult).toFixed(1)} pips`;
}

export function directionLabel(dir: number): string {
  if (dir > 0) return "BUY";
  if (dir < 0) return "SELL";
  return "—";
}

export function patternPretty(p: string): string {
  return p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatWinRate(rate: number | null | undefined): string {
  if (rate == null || !Number.isFinite(rate)) return "—";
  const pct = rate <= 1 ? rate * 100 : rate;
  return `${pct.toFixed(0)}%`;
}

export function formatPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function scoreColor(score: number): string {
  const abs = Math.abs(score);
  if (abs >= 70) return score > 0 ? "var(--tv-green)" : "var(--tv-red)";
  if (abs >= 50) return score > 0 ? "var(--tv-green-bright)" : "var(--tv-red-dim)";
  return "var(--tv-text-muted)";
}

export function biasColor(bias: string): string {
  const b = (bias || "").toLowerCase();
  if (b.includes("bull")) return "var(--tv-green)";
  if (b.includes("bear")) return "var(--tv-red)";
  return "var(--tv-text-muted)";
}

export function pipMult(pair: string): number {
  if (pair.includes("DJI") || pair.includes("US30")) return 1;
  if (pair.includes("JPY")) return 100;
  return 10000;
}

/** Timeframe duration in milliseconds (UTC boundaries). */
export function timeframeMs(tf: string): number {
  switch (tf) {
    case "15m":
      return 15 * 60 * 1000;
    case "1h":
      return 60 * 60 * 1000;
    case "4h":
      return 4 * 60 * 60 * 1000;
    case "1d":
      return 24 * 60 * 60 * 1000;
    default:
      return 60 * 60 * 1000;
  }
}

/**
 * Next candle close time for `tf` (aligned to UTC epoch boundaries).
 * e.g. 1h → top of next hour; 15m → next :00/:15/:30/:45; 4h → 00/04/08/12/16/20 UTC.
 */
export function nextCandleClose(tf: string, now: Date = new Date()): Date {
  const ms = timeframeMs(tf);
  const t = now.getTime();
  const next = Math.ceil(t / ms) * ms;
  // If we're exactly on a boundary, jump one full period ahead
  return new Date(next === t ? next + ms : next);
}

/** Seconds remaining until the next closed candle on `tf`. */
export function secondsToNextClose(tf: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((nextCandleClose(tf, now).getTime() - now.getTime()) / 1000));
}

/** Format remaining seconds as mm:ss or hh:mm:ss. */
export function formatCountdown(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
