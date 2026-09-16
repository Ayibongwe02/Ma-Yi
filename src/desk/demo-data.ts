import type { Candle, LiveCommand, MlStatus, Order, PairBias, Signal } from "./types";

const PAIRS = [
  { pair: "EURUSD=X", px: 1.08462, vol: 0.00042, seed: 0x11a3 },
  { pair: "GBPUSD=X", px: 1.27184, vol: 0.00058, seed: 0x22b4 },
  { pair: "USDJPY=X", px: 148.352, vol: 0.082, seed: 0x33c5 },
  { pair: "GBPJPY=X", px: 188.614, vol: 0.14, seed: 0x44d6 },
  { pair: "^DJI", px: 41248, vol: 42, seed: 0x55e7 },
] as const;

export const DEFAULT_PAIRS = PAIRS.map((p) => p.pair);

function mulberry32(a: number) {
  return () => {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TF_MS: Record<string, number> = {
  "15m": 15 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

const TF_BARS: Record<string, number> = {
  "15m": 160,
  "1h": 140,
  "4h": 90,
  "1d": 70,
};

const cache = new Map<string, Candle[]>();

export function getCandles(pair: string, timeframe = "1h", limit = 120): Candle[] {
  const key = `${pair}|${timeframe}`;
  let all = cache.get(key);
  if (!all) {
    const spec = PAIRS.find((p) => p.pair === pair) ?? PAIRS[0];
    const n = TF_BARS[timeframe] ?? 120;
    const step = TF_MS[timeframe] ?? TF_MS["1h"];
    const rnd = mulberry32(spec.seed + timeframe.length * 97);
    const now = Date.now();
    const start = now - n * step;
    let price = spec.px * (0.992 + rnd() * 0.01);
    all = [];
    for (let i = 0; i < n; i++) {
      const drift = (rnd() - 0.48) * spec.vol;
      const shock = rnd() < 0.04 ? (rnd() - 0.5) * spec.vol * 4 : 0;
      const o = price;
      const c = Math.max(1e-6, o + drift + shock);
      const spread = spec.vol * (0.35 + rnd() * 1.4);
      const h = Math.max(o, c) + spread * rnd();
      const l = Math.min(o, c) - spread * rnd();
      all.push({
        t: new Date(start + i * step).toISOString(),
        o,
        h,
        l,
        c,
        v: Math.floor(80 + rnd() * 980),
      });
      price = c;
    }
    cache.set(key, all);
  }
  return all.slice(-limit);
}

export function lastCandle(pair: string, timeframe = "1h"): Candle | undefined {
  const rows = getCandles(pair, timeframe, 140);
  return rows[rows.length - 1];
}

export function tickFeed(intensity = 1) {
  for (const spec of PAIRS) {
    for (const tf of Object.keys(TF_MS)) {
      const key = `${spec.pair}|${tf}`;
      const all = cache.get(key);
      if (!all?.length) continue;
      const last = all[all.length - 1];
      const drift = (Math.random() - 0.48) * spec.vol * 0.28 * intensity;
      const next = Math.max(1e-6, last.c + drift);
      last.c = next;
      last.h = Math.max(last.h, next);
      last.l = Math.min(last.l, next);
      last.v += Math.floor(Math.random() * 8);
    }
  }
}

export function sessionChange(pair: string, timeframe = "1h"): { last: number; prev: number; pct: number } {
  const rows = getCandles(pair, timeframe, 140);
  const last = rows[rows.length - 1]?.c ?? 0;
  const prev = rows[Math.max(0, rows.length - 25)]?.c ?? last;
  const pct = prev ? ((last - prev) / prev) * 100 : 0;
  return { last, prev, pct };
}

function hoursAgo(h: number): string {
  return new Date(Date.now() - h * 3600_000).toISOString();
}

function mkSignal(partial: Omit<Signal, "fired" | "alerted" | "ts" | "created_at" | "bar_time"> & { hours: number }): Signal {
  const bar = hoursAgo(partial.hours);
  return {
    ...partial,
    ts: bar,
    created_at: bar,
    bar_time: bar,
    fired: true,
    alerted: true,
  };
}

function buildSignals(): Signal[] {
  const eurusd = lastCandle("EURUSD=X")?.c ?? 1.0846;
  const gbpusd = lastCandle("GBPUSD=X")?.c ?? 1.2718;
  const usdjpy = lastCandle("USDJPY=X")?.c ?? 148.35;
  const gbpjpy = lastCandle("GBPJPY=X")?.c ?? 188.61;
  const dji = lastCandle("^DJI")?.c ?? 41248;

  return [
    mkSignal({
      id: 101,
      hours: 1.2,
      pair: "EURUSD=X",
      timeframe: "1h",
      pattern: "engulfing",
      direction: 1,
      raw_score: 78,
      final_score: 82,
      entry: eurusd,
      sl: eurusd - 0.00185,
      tp: eurusd + 0.00278,
      risk: 0.00185,
      rr: 1.5,
      trend: 1,
      sr_score: 0.74,
      volatility: "normal",
      outcome: null,
      meta: { _trend: "bullish", _structure: "BOS up", _zone: "demand fresh", _vol: "ATR mid" },
      second_opinion: {
        final_verdict: "CONFIRM",
        combined_score: 79.4,
        rationale: "HTF bullish, demand zone untested, COT specs adding longs. Engulfing at session low.",
      },
    }),
    mkSignal({
      id: 102,
      hours: 2.4,
      pair: "GBPJPY=X",
      timeframe: "1h",
      pattern: "three_soldiers_crows",
      direction: -1,
      raw_score: 84,
      final_score: 76,
      entry: gbpjpy,
      sl: gbpjpy + 0.28,
      tp: gbpjpy - 0.42,
      risk: 0.28,
      rr: 1.5,
      trend: -1,
      sr_score: 0.68,
      volatility: "expanded",
      outcome: null,
      meta: { _trend: "bearish", _structure: "CHoCH down", _zone: "supply" },
      second_opinion: {
        final_verdict: "CAUTION",
        combined_score: 61.2,
        rationale: "Pattern is clean but GBP COT is mixed. Size down until London close.",
      },
    }),
    mkSignal({
      id: 103,
      hours: 0.8,
      pair: "USDJPY=X",
      timeframe: "15m",
      pattern: "tweezer",
      direction: 1,
      raw_score: 71,
      final_score: 73,
      entry: usdjpy,
      sl: usdjpy - 0.18,
      tp: usdjpy + 0.27,
      risk: 0.18,
      rr: 1.5,
      trend: 1,
      sr_score: 0.61,
      volatility: "normal",
      outcome: null,
      meta: { _trend: "bullish", _sr: "round 148.00 held" },
    }),
    mkSignal({
      id: 201,
      hours: 5.5,
      pair: "GBPUSD=X",
      timeframe: "1h",
      pattern: "shooting_star",
      direction: -1,
      raw_score: 62,
      final_score: 58,
      entry: gbpusd,
      sl: gbpusd + 0.0021,
      tp: gbpusd - 0.00315,
      risk: 0.0021,
      rr: 1.5,
      trend: -1,
      sr_score: 0.55,
      volatility: "compressed",
      outcome: null,
      meta: { _trend: "neutral-bear", _zone: "supply retest" },
    }),
    mkSignal({
      id: 202,
      hours: 7.1,
      pair: "^DJI",
      timeframe: "1h",
      pattern: "engulfing",
      direction: 1,
      raw_score: 66,
      final_score: 54,
      entry: dji,
      sl: dji - 85,
      tp: dji + 128,
      risk: 85,
      rr: 1.5,
      trend: 1,
      sr_score: 0.49,
      volatility: "normal",
      outcome: null,
    }),
    mkSignal({
      id: 203,
      hours: 9.0,
      pair: "EURUSD=X",
      timeframe: "4h",
      pattern: "tweezer",
      direction: -1,
      raw_score: 55,
      final_score: 51,
      entry: eurusd + 0.0004,
      sl: eurusd + 0.0021,
      tp: eurusd - 0.00215,
      risk: 0.0017,
      rr: 1.5,
      trend: 0,
      sr_score: 0.44,
      volatility: "compressed",
      outcome: null,
    }),
    mkSignal({
      id: 301,
      hours: 18,
      pair: "GBPUSD=X",
      timeframe: "1h",
      pattern: "engulfing",
      direction: 1,
      raw_score: 72,
      final_score: 48,
      entry: gbpusd - 0.0012,
      sl: gbpusd - 0.0031,
      tp: gbpusd + 0.0016,
      risk: 0.0019,
      rr: 1.5,
      trend: 1,
      sr_score: 0.4,
      volatility: "normal",
      outcome: "win",
    }),
  ];
}

const SIGNALS = buildSignals();

export function getLiveCommand(): LiveCommand {
  const act_now = SIGNALS.filter((s) => Math.abs(s.final_score) >= 70 && !s.outcome);
  const watch = SIGNALS.filter((s) => Math.abs(s.final_score) >= 50 && Math.abs(s.final_score) < 70 && !s.outcome);
  const pair_bias: PairBias[] = [
    {
      pair: "EURUSD=X",
      label: "EUR/USD",
      bias: "bullish",
      score: 42,
      htf_bias: "bullish",
      stf_bias: "bullish",
      aligned: true,
      structure: "BOS up",
      zone: "demand",
      cot_score: 38,
      sentiment_score: -12,
      divergence_label: "inst. long / retail short",
    },
    {
      pair: "GBPUSD=X",
      label: "GBP/USD",
      bias: "bearish",
      score: -18,
      htf_bias: "bearish",
      stf_bias: "neutral",
      aligned: false,
      structure: "range",
      zone: "mid",
      cot_score: -8,
      sentiment_score: 22,
    },
    {
      pair: "USDJPY=X",
      label: "USD/JPY",
      bias: "bullish",
      score: 27,
      htf_bias: "bullish",
      stf_bias: "bullish",
      aligned: true,
      structure: "higher lows",
      zone: "demand retest",
      cot_score: 14,
    },
    {
      pair: "GBPJPY=X",
      label: "GBP/JPY",
      bias: "bearish",
      score: -33,
      htf_bias: "bearish",
      stf_bias: "bearish",
      aligned: true,
      structure: "CHoCH down",
      zone: "supply",
      cot_score: -22,
    },
    {
      pair: "^DJI",
      label: "US30",
      bias: "bullish",
      score: 11,
      htf_bias: "bullish",
      stf_bias: "neutral",
      aligned: false,
      structure: "trend pause",
      zone: "range high",
      cot_score: null,
    },
  ];
  return {
    act_now,
    watch,
    pair_bias,
    stats: { total: 47, wins: 18, losses: 11, pending: 18, win_rate: 0.62, expectancy: 0.34 },
    bias: { longs: 3, shorts: 2, net: 1 },
    ts: new Date().toISOString(),
  };
}

export const DEMO_ML: MlStatus = {
  health: "healthy",
  n_samples: 246,
  train_acc: 0.71,
  last_train: hoursAgo(3.2),
  message: "Walk-forward stable · auto-retrain idle",
};

export const DEMO_LOGS: string[] = [
  "[12:04:11] scan start  mode=replay  pairs=5  tf=1h",
  "[12:04:12] EURUSD=X  engulfing  BUY  score=+82  fired",
  "[12:04:12] GBPJPY=X  three_soldiers_crows  SELL  score=-76  fired",
  "[12:04:13] USDJPY=X  tweezer  BUY  score=+73  fired",
  "[12:04:13] GBPUSD=X  shooting_star  SELL  score=-58  watch",
  "[12:04:14] pair-bias refresh  aligned=3/5  net=+1",
  "[12:04:14] ml health=healthy  n=246  acc=0.71",
  "[12:04:15] scan complete  act_now=3  watch=3  elapsed=3.8s",
];

export const DEMO_ORDERS: Order[] = [
  { ts: hoursAgo(1.3), pair: "EURUSD=X", side: "BUY", units: 1000, status: "queued", dry_run: true },
  { ts: hoursAgo(2.5), pair: "GBPJPY=X", side: "SELL", units: 500, status: "queued", dry_run: true },
];
