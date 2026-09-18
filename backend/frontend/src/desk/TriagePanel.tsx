import { AlertCircle, Eye, Loader2 } from "lucide-react";
import type { Signal } from "./types";
import {
  cn,
  confidenceLabel,
  directionLabel,
  displayPair,
  formatAge,
  formatPrice,
  patternPretty,
} from "./utils";

interface Props {
  actNow: Signal[];
  watch: Signal[];
  selectedId?: number;
  onSelect: (s: Signal) => void;
  loading?: boolean;
}

export default function TriagePanel({ actNow, watch, selectedId, onSelect, loading }: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-tv-panel">
      <div className="flex items-center justify-between border-b border-tv-border px-3 py-2">
        <div className="text-caption font-semibold uppercase tracking-wider text-tv-muted">Triage</div>
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-tv-muted" />}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <SectionHeader
          icon={<AlertCircle className="h-3.5 w-3.5 text-tv-red" />}
          title="Act Now"
          count={actNow.length}
          color="var(--tv-red)"
        />
        {actNow.length === 0 ? (
          <EmptyRow text={loading ? "Scanning…" : "No high-conviction setups"} />
        ) : (
          actNow.map((s) => (
            <SignalRow key={s.id} s={s} bucket="act_now" selected={selectedId === s.id} onSelect={onSelect} />
          ))
        )}
        <SectionHeader
          icon={<Eye className="h-3.5 w-3.5 text-tv-amber" />}
          title="Watch"
          count={watch.length}
          color="var(--tv-amber)"
        />
        {watch.length === 0 ? (
          <EmptyRow text="No watchlist items" />
        ) : (
          watch.map((s) => (
            <SignalRow key={s.id} s={s} bucket="watch" selected={selectedId === s.id} onSelect={onSelect} />
          ))
        )}
      </div>
    </div>
  );
}

function SectionHeader({
  icon,
  title,
  count,
  color,
}: {
  icon: React.ReactNode;
  title: string;
  count: number;
  color: string;
}) {
  return (
    <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-tv-border bg-tv-panel-2 px-3 py-1.5">
      {icon}
      <span className="text-caption font-semibold" style={{ color }}>
        {title}
      </span>
      <span className="tv-mono ml-auto text-micro text-tv-dim">{count}</span>
    </div>
  );
}

function EmptyRow({ text }: { text: string }) {
  return <div className="px-3 py-4 text-center text-xs text-tv-dim">{text}</div>;
}

function SignalRow({
  s,
  bucket,
  selected,
  onSelect,
}: {
  s: Signal;
  bucket: "act_now" | "watch";
  selected: boolean;
  onSelect: (s: Signal) => void;
}) {
  const isBuy = s.direction > 0;
  return (
    <button
      onClick={() => onSelect(s)}
      className={cn(
        "w-full border-b border-tv-border px-3 py-2.5 text-left transition-colors duration-150 hover:bg-tv-hover",
        selected && "bg-[color-mix(in_oklab,var(--tv-blue)_10%,transparent)]",
        bucket === "act_now" && !selected && "act-now-glow",
        bucket === "watch" && !selected && "watch-glow"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5">
            <span className="text-desk font-semibold">{displayPair(s.pair)}</span>
            <span
              className={cn(
                "tv-mono rounded-sm px-1.5 py-0.5 text-micro font-bold",
                isBuy
                  ? "bg-[color-mix(in_oklab,var(--tv-green)_20%,transparent)] text-tv-green"
                  : "bg-[color-mix(in_oklab,var(--tv-red)_20%,transparent)] text-tv-red"
              )}
            >
              {directionLabel(s.direction)}
            </span>
          </div>
          <div className="mt-0.5 text-caption text-tv-muted">{patternPretty(s.pattern)}</div>
        </div>
        <div className="text-right">
          <div className={cn("tv-mono text-[15px] font-bold", isBuy ? "text-tv-green" : "text-tv-red")}>
            {Math.abs(Number(s.final_score)).toFixed(0)}
          </div>
          <div className="text-micro text-tv-dim">{confidenceLabel(s.final_score)}</div>
        </div>
      </div>
      <div className="tv-mono mt-1.5 flex items-center gap-3 text-micro text-tv-dim">
        <span>E {formatPrice(s.entry, s.pair)}</span>
        <span>SL {formatPrice(s.sl, s.pair)}</span>
        <span className="ml-auto">{formatAge(s.bar_time)}</span>
      </div>
      <div className="score-bar mt-2">
        <div className="score-bar-marker" style={{ left: `${50 + s.final_score / 2}%` }} />
      </div>
    </button>
  );
}
