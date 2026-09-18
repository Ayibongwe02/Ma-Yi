import type { MlStatus, PatternHitRate } from "./types";

export const PATTERN_HIT_RATES: Record<string, PatternHitRate> = {
  doji: { n: 35, wins: 8, win_rate: 22.9 },
  tweezer: { n: 324, wins: 140, win_rate: 43.2 },
  engulfing: { n: 230, wins: 114, win_rate: 49.6 },
  three_soldiers_crows: { n: 205, wins: 137, win_rate: 66.8 },
  inside_bar: { n: 2, wins: 2, win_rate: 100 },
  star: { n: 44, wins: 19, win_rate: 43.2 },
  hammer: { n: 13, wins: 6, win_rate: 46.2 },
  shooting_star: { n: 17, wins: 9, win_rate: 52.9 },
  marubozu: { n: 154, wins: 93, win_rate: 60.4 },
};

export const STAGE4_ML: MlStatus = {
  health: "healthy",
  n_samples: 40,
  train_acc: 0.65,
  walk_forward: 0.58,
  last_train: new Date().toISOString(),
  paused: false,
  explore: false,
  message: "Waiting for backend ML status…",
};

export function sisterOpinion(
  pattern: string,
  rawScore: number,
  trendAligned: boolean
): { final_verdict: "CONFIRM" | "CAUTION" | "VETO"; combined_score: number; rationale: string; source: string } {
  const key = pattern.replace(/_bull|_bear|_top|_bottom/g, "");
  const hit = PATTERN_HIT_RATES[pattern] ?? PATTERN_HIT_RATES[key];
  const wr = hit ? hit.win_rate / 100 : 0.48;
  let combined = rawScore * 0.55 + wr * 100 * 0.35 + (trendAligned ? 8 : -10);
  combined = Math.max(-100, Math.min(100, combined));
  let verdict: "CONFIRM" | "CAUTION" | "VETO" = "CAUTION";
  if (wr < 0.38 || (!trendAligned && wr < 0.5)) verdict = "VETO";
  else if (wr >= 0.55 && trendAligned && Math.abs(rawScore) >= 60) verdict = "CONFIRM";
  const wrLabel = hit ? `${hit.win_rate.toFixed(0)}% of ${hit.n}` : "thin sample";
  const rationale = trendAligned
    ? `${pattern.replace(/_/g, " ")} walk-forward hit-rate ${wrLabel}. Trend aligned — ${verdict.toLowerCase()}.`
    : `${pattern.replace(/_/g, " ")} walk-forward hit-rate ${wrLabel}. Counter-trend — size down or skip.`;
  return { final_verdict: verdict, combined_score: Number(combined.toFixed(1)), rationale, source: "sentinel-ml-v2" };
}
