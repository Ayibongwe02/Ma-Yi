import { create } from "zustand";
import type { LiveCommand, MlStatus, Order, Signal } from "./types";
import { DEFAULT_PAIRS, DEMO_LOGS, DEMO_ORDERS, getCandles, getLiveCommand, tickFeed } from "./demo-data";
import { deriveBias, scanPair } from "./engine";
import { STAGE4_ML } from "./ml";
import { displayPair } from "./utils";
import * as api from "./api";

const KILL_KEY = "mayi-kill-switch";

function stamp(line: string) {
  const t = new Date().toISOString().slice(11, 19);
  return `[${t}] ${line}`;
}

function readKill() {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(KILL_KEY) === "1";
}

function bucket(signals: Signal[]) {
  const pending = signals.filter((s) => !s.outcome);
  const act_now = pending
    .filter((s) => Math.abs(s.final_score) >= 70)
    .sort((a, b) => Math.abs(b.final_score) - Math.abs(a.final_score));
  const watch = pending
    .filter((s) => Math.abs(s.final_score) >= 50 && Math.abs(s.final_score) < 70)
    .sort((a, b) => Math.abs(b.final_score) - Math.abs(a.final_score));
  return { act_now, watch };
}

function assemble(signals: Signal[]): LiveCommand {
  const { act_now, watch } = bucket(signals);
  const pair_bias = DEFAULT_PAIRS.map((pair) =>
    deriveBias(pair, displayPair(pair), getCandles(pair, "1h", 140), [...act_now, ...watch])
  );
  const longs = pair_bias.filter((b) => b.bias === "bullish").length;
  const shorts = pair_bias.filter((b) => b.bias === "bearish").length;
  const wins = 18;
  const losses = 11;
  const pending = act_now.length + watch.length;
  return {
    act_now,
    watch,
    pair_bias,
    stats: {
      total: wins + losses + pending,
      wins,
      losses,
      pending,
      win_rate: wins / (wins + losses),
      expectancy: 0.34,
    },
    bias: { longs, shorts, net: longs - shorts },
    ts: new Date().toISOString(),
  };
}

function dedupeSignals(signals: Signal[]): Signal[] {
  const byKey = new Map<string, Signal>();
  for (const s of signals) {
    const key = `${s.pair}|${s.timeframe}`;
    const prev = byKey.get(key);
    if (!prev || Math.abs(s.final_score) > Math.abs(prev.final_score)) byKey.set(key, s);
  }
  return [...byKey.values()];
}

interface DeskStore {
  live: LiveCommand;
  logs: string[];
  orders: Order[];
  ml: MlStatus;
  killEnabled: boolean;
  killBusy: boolean;
  scanning: boolean;
  feed: number;
  hydrated: boolean;
  backendOnline: boolean | null;
  scan: () => Promise<void>;
  toggleKill: () => void;
  tick: () => void;
  hydrate: () => void;
  refreshFromBackend: () => Promise<void>;
}

export const useDeskStore = create<DeskStore>((set, get) => ({
  live: getLiveCommand(),
  logs: DEMO_LOGS,
  orders: DEMO_ORDERS,
  ml: STAGE4_ML,
  killEnabled: false,
  killBusy: false,
  scanning: false,
  feed: 0,
  hydrated: false,
  backendOnline: null,

  hydrate: () => {
    if (get().hydrated) return;
    set({ killEnabled: readKill(), hydrated: true });
    // Probe backend once and pull live data if available
    void (async () => {
      const online = await api.probeBackend();
      set({ backendOnline: online });
      if (online) {
        await get().refreshFromBackend();
        set((s) => ({
          logs: [...s.logs, stamp("backend online — live data from Ma-yi API")].slice(-120),
        }));
      } else {
        set((s) => ({
          logs: [...s.logs, stamp("backend offline — using synthetic demo feed")].slice(-120),
        }));
      }
    })();
  },

  refreshFromBackend: async () => {
    try {
      const [live, ml, orders, logs, kill] = await Promise.all([
        api.fetchLiveCommand(),
        api.fetchMlStatus().catch(() => STAGE4_ML),
        api.fetchOrders(40).catch(() => get().orders),
        api.fetchEngineLog(80).catch(() => get().logs),
        api.fetchKillSwitch().catch(() => ({ enabled: get().killEnabled })),
      ]);
      set({
        live,
        ml: { ...STAGE4_ML, ...ml },
        orders: Array.isArray(orders) ? orders : get().orders,
        logs: Array.isArray(logs) ? logs.slice(-120) : get().logs,
        killEnabled: !!kill.enabled,
        backendOnline: true,
        feed: get().feed + 1,
      });
      if (typeof window !== "undefined") {
        localStorage.setItem(KILL_KEY, kill.enabled ? "1" : "0");
      }
    } catch (e) {
      console.warn("[desk] refreshFromBackend failed", e);
      set({ backendOnline: false });
    }
  },

  tick: () => {
    // Only advance synthetic feed when offline
    if (get().backendOnline === true) return;
    tickFeed(0.4);
    set((s) => ({ feed: s.feed + 1 }));
  },

  toggleKill: () => {
    const next = !get().killEnabled;
    set({ killBusy: true, killEnabled: next });
    if (typeof window !== "undefined") {
      localStorage.setItem(KILL_KEY, next ? "1" : "0");
    }
    void (async () => {
      try {
        if (get().backendOnline) {
          await api.setKillSwitch(next);
        }
        set((s) => ({
          killBusy: false,
          logs: [...s.logs, stamp(`kill switch ${next ? "ON" : "OFF"}`)].slice(-120),
        }));
      } catch (e) {
        console.warn(e);
        set({ killBusy: false });
      }
    })();
  },

  scan: async () => {
    if (get().scanning) return;
    set({ scanning: true });
    const started = performance.now();

    // Prefer real backend scan (yfinance + engine + ML)
    if (get().backendOnline !== false) {
      try {
        const online = get().backendOnline === true || (await api.probeBackend());
        if (online) {
          set({ backendOnline: true });
          await api.triggerScan({ mode: "replay", refresh_bias: true });
          await get().refreshFromBackend();
          const elapsed = ((performance.now() - started) / 1000).toFixed(1);
          set((s) => ({
            scanning: false,
            logs: [
              ...s.logs,
              stamp(`backend scan complete  act_now=${s.live.act_now.length}  watch=${s.live.watch.length}  elapsed=${elapsed}s`),
            ].slice(-120),
            feed: s.feed + 1,
          }));
          return;
        }
      } catch (e) {
        console.warn("[desk] backend scan failed, falling back to local engine", e);
        set({ backendOnline: false });
      }
    }

    // Local synthetic scan (demo mode)
    await new Promise((r) => window.setTimeout(r, 600 + Math.random() * 400));

    const found: Signal[] = [];
    const lines: string[] = [];
    for (const pair of DEFAULT_PAIRS) {
      for (const tf of ["1h", "15m"] as const) {
        const candles = getCandles(pair, tf, 140);
        const hits = scanPair(pair, tf, candles);
        found.push(...hits);
        for (const h of hits) {
          const side = h.direction > 0 ? "BUY" : "SELL";
          const bucketName = Math.abs(h.final_score) >= 70 ? "fired" : "watch";
          lines.push(
            stamp(
              `${pair}  ${h.pattern}  ${side}  score=${h.final_score > 0 ? "+" : ""}${h.final_score.toFixed(0)}  ${bucketName}`
            )
          );
        }
      }
    }

    const unique = dedupeSignals(found);
    const live = assemble(unique);
    const { killEnabled, orders } = get();
    const nextOrders: Order[] = [...orders];

    if (killEnabled) {
      lines.push(stamp("kill switch ON — skip dry-run orders"));
    } else {
      for (const s of live.act_now) {
        nextOrders.unshift({
          id: `ord-${s.id}`,
          ts: new Date().toISOString(),
          pair: s.pair,
          side: s.direction > 0 ? "BUY" : "SELL",
          units: s.pair.includes("DJI") ? 1 : 1000,
          entry: s.entry,
          sl: s.sl,
          tp: s.tp,
          status: "queued",
          dry_run: true,
          signal_id: s.id,
          reason: s.pattern,
        });
        lines.push(stamp(`order queued  ${s.pair}  ${s.direction > 0 ? "BUY" : "SELL"}  dry-run`));
      }
    }

    const elapsed = ((performance.now() - started) / 1000).toFixed(1);
    const aligned = (live.pair_bias ?? []).filter((b) => b.aligned).length;
    lines.push(stamp(`pair-bias refresh  aligned=${aligned}/${live.pair_bias?.length ?? 0}  net=${live.bias?.net ?? 0}`));
    lines.push(
      stamp(
        `ml health=${STAGE4_ML.health}  n=${STAGE4_ML.n_samples}  wf=${((STAGE4_ML.walk_forward ?? 0) * 100).toFixed(0)}%  paused=${STAGE4_ML.paused}`
      )
    );
    lines.push(stamp(`local scan complete  act_now=${live.act_now.length}  watch=${live.watch.length}  elapsed=${elapsed}s`));

    set((s) => ({
      live,
      logs: [...s.logs, ...lines].slice(-120),
      orders: nextOrders.slice(0, 40),
      scanning: false,
      feed: s.feed + 1,
      ml: { ...STAGE4_ML },
    }));
  },
}));
