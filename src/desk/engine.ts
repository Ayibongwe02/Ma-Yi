import type { Candle, PairBias, Signal } from "./types";
import { displayPair } from "./utils";
import { sisterOpinion } from "./ml";

type Bar = Pick<Candle, "o" | "h" | "l" | "c">;

const body = (r: Bar) => Math.abs(r.c - r.o);
const range = (r: Bar) => Math.max(r.h - r.l, 1e-12);
const upperWick = (r: Bar) => r.h - Math.max(r.o, r.c);
const lowerWick = (r: Bar) => Math.min(r.o, r.c) - r.l;
const isBull = (r: Bar) => r.c > r.o;

function avgBody(rows: Bar[], i: number, lookback = 14) {
  const start = Math.max(0, i - lookback);
  const window = rows.slice(start, i);
  if (!window.length) return body(rows[i]) || 1e-12;
  const m = window.reduce((s, r) => s + body(r), 0) / window.length;
  return m || 1e-12;
}

function scoreHammer(row: Bar, avg: number) {
  const b = body(row);
  const rng = range(row);
  const lower = lowerWick(row);
  const upper = upperWick(row);
  if (rng === 0 || b / rng > 0.35) return 0;
  if (lower < b * 2) return 0;
  if (upper > b * 0.6) return 0;
  const ratio = (Math.min(lower / Math.max(b, rng * 0.05), 5) / 5) * 70;
  const size = (Math.min(b / avg, 1.5) / 1.5) * 30;
  return Math.round((ratio + size) * 10) / 10;
}

function scoreShootingStar(row: Bar, avg: number) {
  const b = body(row);
  const rng = range(row);
  const lower = lowerWick(row);
  const upper = upperWick(row);
  if (rng === 0 || b / rng > 0.35) return 0;
  if (upper < b * 2) return 0;
  if (lower > b * 0.6) return 0;
  const ratio = (Math.min(upper / Math.max(b, rng * 0.05), 5) / 5) * 70;
  const size = (Math.min(b / avg, 1.5) / 1.5) * 30;
  return -Math.round((ratio + size) * 10) / 10;
}

function scoreDoji(row: Bar) {
  const rng = range(row);
  if (rng === 0) return 0;
  const pct = body(row) / rng;
  if (pct > 0.1) return 0;
  return Math.round((1 - pct / 0.1) * 1000) / 10;
}

function scoreMarubozu(row: Bar, avg: number) {
  const rng = range(row);
  if (rng === 0) return 0;
  const wickPct = (upperWick(row) + lowerWick(row)) / rng;
  if (wickPct > 0.08) return 0;
  const size = (Math.min(body(row) / avg, 2) / 2) * 100;
  const score = Math.round(size * (1 - wickPct / 0.08) * 10) / 10;
  return isBull(row) ? score : -score;
}

function scoreEngulfing(prev: Bar, curr: Bar) {
  const pb = body(prev);
  const cb = body(curr);
  if (!pb || !cb) return 0;
  const bull = !isBull(prev) && isBull(curr) && curr.o <= prev.c && curr.c >= prev.o;
  const bear = isBull(prev) && !isBull(curr) && curr.o >= prev.c && curr.c <= prev.o;
  if (!bull && !bear) return 0;
  const size = (Math.min(cb / pb, 3) / 3) * 100;
  const s = Math.round(size * 10) / 10;
  return bull ? s : -s;
}

function scoreTweezer(prev: Bar, curr: Bar, tol: number) {
  const highDiff = Math.abs(prev.h - curr.h) / Math.max(prev.h, 1e-9);
  const lowDiff = Math.abs(prev.l - curr.l) / Math.max(prev.l, 1e-9);
  if (highDiff < tol && isBull(prev) && !isBull(curr)) {
    return -Math.round((1 - highDiff / tol) * 1000) / 10;
  }
  if (lowDiff < tol && !isBull(prev) && isBull(curr)) {
    return Math.round((1 - lowDiff / tol) * 1000) / 10;
  }
  return 0;
}

function scoreStar(c1: Bar, c2: Bar, c3: Bar, avg: number, bullish: boolean) {
  const b1 = body(c1);
  const b2 = body(c2);
  const b3 = body(c3);
  if (!b1 || !b3) return 0;
  if (bullish) {
    if (isBull(c1) || b1 < avg * 0.8) return 0;
    if (b2 > b1 * 0.4) return 0;
    if (!isBull(c3)) return 0;
    const pen = (c3.c - c1.c) / b1;
    if (pen < 0.3) return 0;
    return Math.round((Math.min(pen, 1.5) / 1.5) * 1000) / 10;
  }
  if (!isBull(c1) || b1 < avg * 0.8) return 0;
  if (b2 > b1 * 0.4) return 0;
  if (isBull(c3)) return 0;
  const pen = (c1.c - c3.c) / b1;
  if (pen < 0.3) return 0;
  return -Math.round((Math.min(pen, 1.5) / 1.5) * 1000) / 10;
}

function scoreThree(c1: Bar, c2: Bar, c3: Bar, avg: number) {
  const bodies = [body(c1), body(c2), body(c3)];
  if (bodies.some((b) => b < avg * 0.5)) return 0;
  const allBull = isBull(c1) && isBull(c2) && isBull(c3);
  const allBear = !isBull(c1) && !isBull(c2) && !isBull(c3);
  if (allBull) {
    const rising = c2.o > c1.o && c2.c > c1.c && c3.o > c2.o && c3.c > c2.c;
    if (!rising) return 0;
    return Math.round((Math.min(bodies.reduce((a, b) => a + b, 0) / (avg * 3), 2) / 2) * 1000) / 10;
  }
  if (allBear) {
    const falling = c2.o < c1.o && c2.c < c1.c && c3.o < c2.o && c3.c < c2.c;
    if (!falling) return 0;
    return -Math.round((Math.min(bodies.reduce((a, b) => a + b, 0) / (avg * 3), 2) / 2) * 1000) / 10;
  }
  return 0;
}

export interface PatternHit {
  i: number;
  pattern: string;
  score: number;
}

export function detectPatterns(candles: Candle[], tweezerTol = 0.0008): PatternHit[] {
  const hits: PatternHit[] = [];
  const n = candles.length;
  for (let i = 2; i < n; i++) {
    const row = candles[i];
    const avg = avgBody(candles, i);
    const scored: [string, number][] = [
      ["hammer", scoreHammer(row, avg)],
      ["shooting_star", scoreShootingStar(row, avg)],
      ["doji", scoreDoji(row)],
      ["marubozu", scoreMarubozu(row, avg)],
      ["engulfing", scoreEngulfing(candles[i - 1], row)],
      ["tweezer", scoreTweezer(candles[i - 1], row, tweezerTol)],
      ["star", scoreStar(candles[i - 2], candles[i - 1], row, avg, true) || scoreStar(candles[i - 2], candles[i - 1], row, avg, false)],
      ["three_soldiers_crows", scoreThree(candles[i - 2], candles[i - 1], row, avg)],
    ];
    let best: [string, number] | null = null;
    for (const s of scored) {
      if (Math.abs(s[1]) < 40) continue;
      if (!best || Math.abs(s[1]) > Math.abs(best[1])) best = s;
    }
    if (best) hits.push({ i, pattern: best[0], score: best[1] });
  }
  return hits;
}

function sma(values: number[], n: number) {
  if (values.length < n) return values.reduce((a, b) => a + b, 0) / values.length;
  const slice = values.slice(-n);
  return slice.reduce((a, b) => a + b, 0) / n;
}

function atr(candles: Candle[], n = 14) {
  const start = Math.max(1, candles.length - n);
  let sum = 0;
  let count = 0;
  for (let i = start; i < candles.length; i++) {
    const c = candles[i];
    const prev = candles[i - 1];
    const tr = Math.max(c.h - c.l, Math.abs(c.h - prev.c), Math.abs(c.l - prev.c));
    sum += tr;
    count++;
  }
  return count ? sum / count : candles[candles.length - 1].h - candles[candles.length - 1].l;
}

function tweezerTol(pair: string) {
  if (pair.includes("DJI")) return 0.0012;
  if (pair.includes("JPY")) return 0.00045;
  return 0.0008;
}

function pip(pair: string) {
  if (pair.includes("DJI")) return 1;
  if (pair.includes("JPY")) return 0.01;
  return 0.0001;
}

let nextId = 400;

export function scanPair(pair: string, timeframe: string, candles: Candle[]): Signal[] {
  if (candles.length < 20) return [];
  const hits = detectPatterns(candles, tweezerTol(pair));
  const closes = candles.map((c) => c.c);
  const ma20 = sma(closes, 20);
  const ma50 = sma(closes, Math.min(50, closes.length));
  const last = candles[candles.length - 1];
  const trend = last.c > ma20 && ma20 >= ma50 ? 1 : last.c < ma20 && ma20 <= ma50 ? -1 : 0;
  const recent = candles.slice(-40);
  const lo = Math.min(...recent.map((c) => c.l));
  const hi = Math.max(...recent.map((c) => c.h));
  const span = hi - lo || 1;
  const volAtr = atr(candles);
  const volMean = atr(candles.slice(0, Math.max(15, candles.length - 20)), 14) || volAtr;
  const volRatio = volAtr / (volMean || volAtr);
  const volatility = volRatio > 1.35 ? "expanded" : volRatio < 0.75 ? "compressed" : "normal";

  const recentHits = hits.filter((h) => h.i >= candles.length - 8);
  const picked = recentHits
    .sort((a, b) => Math.abs(b.score) - Math.abs(a.score))
    .slice(0, 2);

  return picked.map((hit) => {
    const bar = candles[hit.i];
    const dir = hit.score >= 0 ? 1 : -1;
    const aligned = trend === 0 ? true : dir === trend;
    const distToExt = dir > 0 ? (bar.c - lo) / span : (hi - bar.c) / span;
    const sr = Math.max(0.2, Math.min(0.95, 1 - distToExt * 0.7));
    let final = Math.abs(hit.score) * 0.62 + sr * 22 + (aligned ? 12 : -14);
    if (volatility === "expanded") final -= 6;
    if (volatility === "compressed") final += 3;
    final = Math.max(20, Math.min(92, final));
    if (dir < 0) {
      /* keep magnitude; sign lives in direction */
    }
    const risk = Math.max(volAtr * 1.15, pip(pair) * 8);
    const rr = 1.5;
    const entry = bar.c;
    const sl = dir > 0 ? entry - risk : entry + risk;
    const tp = dir > 0 ? entry + risk * rr : entry - risk * rr;
    const opinion = sisterOpinion(hit.pattern, Math.abs(hit.score), aligned);
    if (opinion.final_verdict === "VETO") final = Math.min(final, 48);
    if (opinion.final_verdict === "CONFIRM") final = Math.min(92, final + 6);
    const signed = dir > 0 ? final : -final;
    const id = nextId++;
    const barTime = bar.t;
    return {
      id,
      ts: new Date().toISOString(),
      created_at: new Date().toISOString(),
      pair,
      timeframe,
      bar_time: barTime,
      pattern: hit.pattern,
      direction: dir,
      raw_score: Math.abs(hit.score),
      final_score: Number(signed.toFixed(1)),
      fired: Math.abs(signed) >= 50,
      entry,
      sl,
      tp,
      risk,
      rr,
      trend,
      sr_score: Number(sr.toFixed(2)),
      volatility,
      outcome: null,
      alerted: Math.abs(signed) >= 70,
      meta: {
        _trend: trend > 0 ? "bullish" : trend < 0 ? "bearish" : "neutral",
        _structure: trend > 0 ? "BOS up" : trend < 0 ? "CHoCH down" : "range",
        _zone: dir > 0 ? "demand" : "supply",
        _vol: volatility,
        _aligned: aligned,
        _atr: Number(volAtr.toFixed(6)),
      },
      second_opinion: opinion,
    } satisfies Signal;
  });
}

export function deriveBias(pair: string, label: string, candles: Candle[], signals: Signal[]): PairBias {
  const closes = candles.map((c) => c.c);
  const ma20 = sma(closes, 20);
  const ma50 = sma(closes, Math.min(50, closes.length));
  const last = closes[closes.length - 1];
  const htf = last > ma50 ? "bullish" : last < ma50 ? "bearish" : "neutral";
  const stf = last > ma20 ? "bullish" : last < ma20 ? "bearish" : "neutral";
  const aligned = htf === stf && htf !== "neutral";
  const top = signals.find((s) => s.pair === pair);
  const score = top ? top.final_score * 0.45 : htf === "bullish" ? 12 : htf === "bearish" ? -12 : 0;
  return {
    pair,
    label: label || displayPair(pair),
    bias: htf,
    score: Number(score.toFixed(0)),
    htf_bias: htf,
    stf_bias: stf,
    aligned,
    structure: aligned ? (htf === "bullish" ? "BOS up" : "CHoCH down") : "range",
    zone: htf === "bullish" ? "demand" : htf === "bearish" ? "supply" : "mid",
    cot_score: Math.round(score * 0.7),
    sentiment_score: ((pair.charCodeAt(0) * 17 + pair.length * 13) % 61) - 30,
    divergence_label: aligned ? undefined : "HTF / STF mixed",
  };
}
