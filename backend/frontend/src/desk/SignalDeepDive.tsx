import { ArrowLeft, Target, Shield, TrendingUp, Activity, Layers, Brain } from "lucide-react";
import type { MlStatus, PairBias, Signal } from "./types";
import {
  biasColor,
  cn,
  confidenceLabel,
  directionLabel,
  displayPair,
  formatAge,
  formatPips,
  formatPrice,
  formatTs,
  patternPretty,
  scoreColor,
} from "./utils";
import { PATTERN_HIT_RATES } from "./ml";

interface Props {
  signal: Signal | null;
  onBack: () => void;
  pairBias: PairBias[];
  ml?: MlStatus;
}

export default function SignalDeepDive({ signal, onBack, pairBias, ml }: Props) {
  if (!signal) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-tv-bg text-tv-muted">
        <Target className="h-10 w-10 opacity-40" />
        <p className="text-sm">Select a signal from the triage panel to deep-dive</p>
        <button onClick={onBack} className="text-xs text-tv-blue hover:underline">
          Back to desk
        </button>
      </div>
    );
  }

  const bias = pairBias.find((b) => b.pair === signal.pair);
  const isBuy = signal.direction > 0;
  const meta = signal.meta || {};
  const conf = confidenceLabel(signal.final_score);
  const hit = PATTERN_HIT_RATES[signal.pattern];

  return (
    <div className="h-full overflow-y-auto bg-tv-bg">
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-tv-border bg-tv-panel px-4 py-3">
        <button onClick={onBack} className="rounded-sm p-1.5 text-tv-muted hover:bg-tv-hover" aria-label="Back">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold">{displayPair(signal.pair)}</span>
            <span
              className={cn(
                "tv-mono rounded-sm px-2 py-0.5 text-caption font-bold",
                isBuy
                  ? "bg-tv-green text-white"
                  : "bg-tv-red text-white"
              )}
            >
              {directionLabel(signal.direction)}
            </span>
            <span className="text-caption text-tv-muted">{signal.timeframe}</span>
          </div>
          <div className="text-xs text-tv-muted">
            {patternPretty(signal.pattern)} · {formatAge(signal.bar_time)}
          </div>
        </div>
        <div className="text-right">
          <div className="tv-mono text-[22px] font-bold leading-none" style={{ color: scoreColor(signal.final_score) }}>
            {signal.final_score > 0 ? "+" : ""}
            {Number(signal.final_score).toFixed(0)}
          </div>
          <div className="text-caption text-tv-muted">{conf}</div>
        </div>
      </div>

      <div className="mx-auto max-w-3xl space-y-4 p-4">
        <section className="tv-panel overflow-hidden rounded-md">
          <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
            <Target className="h-3.5 w-3.5" />
            Trade levels
          </div>
          <div className="grid grid-cols-3 divide-x divide-tv-border">
            <LevelBlock label="Entry" value={formatPrice(signal.entry, signal.pair)} color="var(--tv-blue)" />
            <LevelBlock
              label="Stop Loss"
              value={formatPrice(signal.sl, signal.pair)}
              color="var(--tv-red)"
              sub={formatPips(signal.risk, signal.pair)}
            />
            <LevelBlock
              label="Take Profit"
              value={formatPrice(signal.tp, signal.pair)}
              color="var(--tv-green)"
              sub={`R:R ${signal.rr?.toFixed(2) ?? "—"}`}
            />
          </div>
        </section>

        <section className="tv-panel overflow-hidden rounded-md">
          <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
            <Activity className="h-3.5 w-3.5" />
            Score breakdown
          </div>
          <div className="space-y-3 p-4">
            <ScoreRow label="Raw pattern" value={signal.raw_score} max={100} />
            <ScoreRow label="Final (context)" value={signal.final_score} max={100} signed />
            <div className="grid grid-cols-2 gap-3 pt-2">
              <MetaPill label="Trend" value={trendLabel(signal.trend)} />
              <MetaPill label="S/R score" value={`${(signal.sr_score * 100).toFixed(0)}%`} />
              <MetaPill label="Volatility" value={signal.volatility || "—"} />
              <MetaPill label="Outcome" value={signal.outcome || "pending"} />
            </div>
          </div>
        </section>

        <section className="tv-panel overflow-hidden rounded-md">
          <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
            <Layers className="h-3.5 w-3.5" />
            Context filters
          </div>
          <div className="grid grid-cols-2 gap-2 p-4">
            {Object.entries(meta).length === 0 && (
              <div className="col-span-2 py-2 text-center text-xs text-tv-dim">No extended meta on this signal</div>
            )}
            {Object.entries(meta)
              .slice(0, 8)
              .map(([k, v]) => (
                <div key={k} className="flex justify-between rounded-sm bg-tv-panel-2 px-3 py-2 text-xs">
                  <span className="text-tv-muted">{k.replace(/^_/, "")}</span>
                  <span className="tv-mono font-medium">{String(v)}</span>
                </div>
              ))}
          </div>
        </section>

        {bias && (
          <section className="tv-panel overflow-hidden rounded-md">
            <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
              <TrendingUp className="h-3.5 w-3.5" />
              Multi-TF / Structure bias
            </div>
            <div className="flex flex-wrap items-center gap-4 p-4">
              <div className="text-lg font-bold capitalize" style={{ color: biasColor(bias.bias) }}>
                {bias.bias}
              </div>
              <div className="tv-mono text-sm" style={{ color: biasColor(bias.bias) }}>
                {bias.score > 0 ? "+" : ""}
                {bias.score != null ? Number(bias.score).toFixed(0) : "—"}
              </div>
              {bias.structure && <div className="text-xs text-tv-muted">{bias.structure}</div>}
              {bias.zone && <div className="text-xs text-tv-muted">Zone: {bias.zone}</div>}
              {bias.divergence_label && <div className="text-xs text-tv-amber">{bias.divergence_label}</div>}
            </div>
          </section>
        )}

        {signal.second_opinion && (
          <section className="tv-panel overflow-hidden rounded-md">
            <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
              <Shield className="h-3.5 w-3.5" />
              Sister system (Sentinel)
            </div>
            <div className="space-y-2 p-4">
              <div className="flex items-center gap-2">
                <VerdictBadge v={signal.second_opinion.final_verdict} />
                <span className="tv-mono text-desk">{signal.second_opinion.combined_score != null ? Number(signal.second_opinion.combined_score).toFixed(1) : "—"}</span>
              </div>
              {signal.second_opinion.rationale && (
                <p className="text-xs leading-relaxed text-tv-muted">{signal.second_opinion.rationale}</p>
              )}
            </div>
          </section>
        )}

        <section className="tv-panel overflow-hidden rounded-md">
          <div className="flex items-center gap-2 border-b border-tv-border px-4 py-2 text-caption font-semibold uppercase tracking-wider text-tv-muted">
            <Brain className="h-3.5 w-3.5" />
            Stage 4 ML health
          </div>
          <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-4">
            <MetaPill label="Health" value={ml?.health ?? "—"} />
            <MetaPill label="Samples" value={ml?.n_samples != null ? String(ml.n_samples) : "—"} />
            <MetaPill label="Train acc" value={ml?.train_acc != null ? `${(ml.train_acc * 100).toFixed(0)}%` : "—"} />
            <MetaPill
              label="Walk-forward"
              value={ml?.walk_forward != null ? `${(ml.walk_forward * 100).toFixed(0)}%` : "—"}
            />
          </div>
          {hit && (
            <div className="border-t border-tv-border px-4 py-3 text-xs text-tv-muted">
              {patternPretty(signal.pattern)} historical hit-rate{" "}
              <span className="tv-mono text-tv-text">
                {hit.win_rate.toFixed(0)}% ({hit.wins}/{hit.n})
              </span>
              . Auto-retrain {ml?.paused ? "paused" : "active"}
              {ml?.explore ? " · explore mode" : ""}.
            </div>
          )}
          {ml?.message && <p className="border-t border-tv-border px-4 py-3 text-xs leading-relaxed text-tv-muted">{ml.message}</p>}
        </section>

        <div className="tv-mono flex flex-wrap gap-x-4 gap-y-1 px-1 text-caption text-tv-dim">
          <span>Bar {formatTs(signal.bar_time, "yyyy-MM-dd HH:mm")}</span>
          <span>Logged {formatTs(signal.ts || signal.created_at, "yyyy-MM-dd HH:mm:ss")}</span>
          <span>ID #{signal.id}</span>
        </div>
      </div>
    </div>
  );
}

function LevelBlock({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div className="px-4 py-4 text-center">
      <div className="text-micro uppercase tracking-wider text-tv-muted">{label}</div>
      <div className="tv-mono mt-1 text-lg font-bold" style={{ color }}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-caption text-tv-dim">{sub}</div>}
    </div>
  );
}

function ScoreRow({ label, value, max, signed }: { label: string; value: number; max: number; signed?: boolean }) {
  const pct = Math.min(100, (Math.abs(value) / max) * 100);
  const color = value >= 0 ? "var(--tv-green)" : "var(--tv-red)";
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-tv-muted">{label}</span>
        <span className="tv-mono font-medium" style={{ color }}>
          {signed && value > 0 ? "+" : ""}
          {Number(value).toFixed(1)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-tv-panel-2">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

function MetaPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between rounded-sm bg-tv-panel-2 px-3 py-2 text-xs">
      <span className="text-tv-muted">{label}</span>
      <span className="tv-mono font-medium capitalize">{value}</span>
    </div>
  );
}

function VerdictBadge({ v }: { v: string }) {
  const map: Record<string, string> = {
    CONFIRM: "bg-[color-mix(in_oklab,var(--tv-green)_20%,transparent)] text-tv-green",
    CAUTION: "bg-[color-mix(in_oklab,var(--tv-amber)_20%,transparent)] text-tv-amber",
    VETO: "bg-[color-mix(in_oklab,var(--tv-red)_20%,transparent)] text-tv-red",
  };
  return <span className={cn("rounded-sm px-2 py-0.5 text-caption font-bold", map[v] || "")}>{v}</span>;
}

function trendLabel(t: number): string {
  if (t > 0) return "Bullish";
  if (t < 0) return "Bearish";
  return "Neutral";
}
