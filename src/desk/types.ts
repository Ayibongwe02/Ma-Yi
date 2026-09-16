export interface Signal {
  id: number;
  ts: string;
  pair: string;
  timeframe: string;
  bar_time: string;
  pattern: string;
  direction: number;
  raw_score: number;
  final_score: number;
  fired: boolean;
  entry: number;
  sl: number;
  tp: number;
  risk: number;
  rr: number;
  trend: number;
  sr_score: number;
  volatility: string;
  outcome: string | null;
  alerted: boolean;
  meta?: Record<string, unknown>;
  created_at?: string;
  second_opinion?: SecondOpinion | null;
}

export interface SecondOpinion {
  final_verdict: "CONFIRM" | "CAUTION" | "VETO";
  combined_score: number;
  rationale: string;
  source?: string;
}

export interface PairBias {
  pair: string;
  label: string;
  bias: string;
  score: number;
  htf_bias?: string;
  stf_bias?: string;
  aligned?: boolean;
  structure?: string;
  zone?: string;
  cot_score?: number | null;
  sentiment_score?: number | null;
  divergence_label?: string;
}

export interface LiveCommand {
  act_now: Signal[];
  watch: Signal[];
  stats?: Stats;
  pair_bias?: PairBias[];
  bias?: { longs: number; shorts: number; net: number };
  ts?: string;
}

export interface Stats {
  total: number;
  wins: number;
  losses: number;
  pending: number;
  win_rate: number;
  expectancy?: number;
}

export interface MlStatus {
  health: string;
  health_detail?: {
    state: string;
    detail?: unknown;
    learning_paused?: boolean;
    explore_mode?: boolean;
    hyper_mode?: string;
  } | null;
  n_samples?: number;
  train_acc?: number;
  walk_forward?: number;
  walk_forward_detail?: {
    available?: boolean;
    n_folds?: number;
    accuracy?: number;
    [k: string]: unknown;
  } | null;
  last_train?: string;
  paused?: boolean;
  explore?: boolean;
  message?: string;
}

export interface Order {
  id?: string;
  ts: string;
  pair: string;
  side: string;
  units?: number;
  entry?: number;
  sl?: number;
  tp?: number;
  status: string;
  dry_run?: boolean;
  signal_id?: number;
  reason?: string;
}

export interface Candle {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export type TvTheme = "dark" | "black" | "light";
export type DrawTool = "cursor" | "hline" | "trend" | "fib";
export type MobilePane = "watch" | "chart" | "triage";

export interface ChartDrawing {
  id: string;
  tool: "hline" | "trend" | "fib";
  i1: number;
  p1: number;
  i2?: number;
  p2?: number;
}

export interface PatternHitRate {
  n: number;
  wins: number;
  win_rate: number;
}
