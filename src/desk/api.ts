/**
 * Client for the Ma-yi Python FastAPI backend.
 * Falls back to demo-data when the backend is unreachable.
 */
import type { Candle, LiveCommand, MlStatus, Order, PairBias, Signal, Stats } from "./types";

const API_BASE = (typeof import.meta !== "undefined" && (import.meta as any).env?.VITE_API_BASE) || "";

async function getJSON<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(path, API_BASE || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000"));
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString(), {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(25_000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const url = new URL(path, API_BASE || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000"));
  const res = await fetch(url.toString(), {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} for ${path}`);
  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<{ status: string; [k: string]: unknown }> {
  return getJSON("/api/health");
}

export async function fetchLiveCommand(): Promise<LiveCommand> {
  return getJSON("/api/live-command");
}

export async function fetchSignals(params?: {
  limit?: number;
  pair?: string;
  min_score?: number;
}): Promise<Signal[]> {
  const data = await getJSON<Signal[] | { signals: Signal[] }>("/api/signals", params as any);
  return Array.isArray(data) ? data : data.signals ?? [];
}

export async function fetchStats(): Promise<Stats> {
  return getJSON("/api/stats");
}

export async function fetchPairBias(): Promise<PairBias[]> {
  const data = await getJSON<PairBias[] | { pairs: PairBias[] }>("/api/pair-bias");
  return Array.isArray(data) ? data : (data as any).pairs ?? [];
}

export async function fetchMlStatus(): Promise<MlStatus> {
  return getJSON("/api/ml/status");
}

export async function fetchOrders(limit = 50): Promise<Order[]> {
  const data = await getJSON<Order[] | { orders: Order[] }>("/api/orders", { limit });
  return Array.isArray(data) ? data : (data as any).orders ?? [];
}

export async function fetchEngineLog(lines = 100): Promise<string[]> {
  const data = await getJSON<string[] | { lines: string[] }>("/api/engine-log", { lines });
  return Array.isArray(data) ? data : (data as any).lines ?? [];
}

export async function triggerScan(body?: {
  pair?: string;
  timeframe?: string;
  mode?: string;
  refresh_bias?: boolean;
}) {
  return postJSON("/api/scan", body ?? { mode: "replay", refresh_bias: true });
}

export async function setKillSwitch(enabled: boolean) {
  return postJSON("/api/kill-switch", { enabled });
}

export async function fetchKillSwitch(): Promise<{ enabled: boolean }> {
  return getJSON("/api/kill-switch");
}

export async function fetchCandles(
  pair: string,
  timeframe = "1h",
  limit = 140
): Promise<Candle[]> {
  // Backend: GET /api/candles/{pair}?timeframe=&limit=
  const encoded = encodeURIComponent(pair);
  const data = await getJSON<Candle[] | { candles: Candle[] }>(`/api/candles/${encoded}`, {
    timeframe,
    limit,
  });
  const rows = Array.isArray(data) ? data : (data as any).candles ?? [];
  // Normalize field names if backend uses different keys
  return rows.map((r: any) => ({
    t: r.t ?? r.time ?? r.datetime ?? r.date ?? "",
    o: Number(r.o ?? r.open),
    h: Number(r.h ?? r.high),
    l: Number(r.l ?? r.low),
    c: Number(r.c ?? r.close),
    v: Number(r.v ?? r.volume ?? 0),
  }));
}

export async function fetchMlInsights() {
  return getJSON("/api/ml/insights");
}

/** Returns true if backend answered /api/health within timeout. */
export async function probeBackend(): Promise<boolean> {
  try {
    await fetchHealth();
    return true;
  } catch {
    return false;
  }
}
