import { TrendingDown, TrendingUp, Minus } from "lucide-react";
import type { PairBias, Signal } from "./types";
import { biasColor, cn, directionLabel, displayPair, formatPct, formatPrice } from "./utils";
import { DEFAULT_PAIRS, getCandles, sessionChange } from "./demo-data";
import { useDeskStore } from "./store";

interface Props {
  activePair: string;
  onSelectPair: (p: string) => void;
  pairBias: PairBias[];
  actNow: Signal[];
  watch: Signal[];
}

export default function WatchlistPanel({
  activePair,
  onSelectPair,
  pairBias,
  actNow,
  watch,
}: Props) {
  const feed = useDeskStore((s) => s.feed);
  const biasMap = Object.fromEntries((pairBias || []).map((b) => [b.pair, b]));

  const scoreFor = (pair: string) => {
    const hits = [...actNow, ...watch].filter((s) => s.pair === pair);
    if (!hits.length) return null;
    return hits.sort((a, b) => Math.abs(b.final_score) - Math.abs(a.final_score))[0];
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-tv-panel" data-feed={feed}>
      <div className="flex items-center justify-between border-b border-tv-border px-3 py-2">
        <div className="text-caption font-semibold uppercase tracking-wider text-tv-muted">Watchlist</div>
        <span className="text-micro text-tv-dim">{DEFAULT_PAIRS.length}</span>
      </div>

      <div className="grid grid-cols-[1fr_auto_auto] gap-x-2 border-b border-tv-border px-3 py-1 text-micro uppercase tracking-wider text-tv-dim">
        <span>Symbol</span>
        <span className="text-right">Last</span>
        <span className="w-14 text-right">Chg%</span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {DEFAULT_PAIRS.map((pair) => {
          const bias = biasMap[pair];
          const top = scoreFor(pair);
          const isActive = activePair === pair;
          const chg = sessionChange(pair, "1h");
          const up = chg.pct >= 0;
          const spark = getCandles(pair, "1h", 28).map((c) => c.c);
          const biasStr = (bias?.bias || "neutral").toLowerCase();

          return (
            <button
              key={pair}
              onClick={() => onSelectPair(pair)}
              className={cn(
                "w-full border-b border-tv-border px-3 py-2 text-left transition-colors duration-150",
                isActive
                  ? "border-l-2 border-l-tv-blue bg-[color-mix(in_oklab,var(--tv-blue)_12%,transparent)]"
                  : "border-l-2 border-l-transparent hover:bg-tv-hover"
              )}
            >
              <div className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-2">
                <span className="text-desk font-semibold">{displayPair(pair)}</span>
                <span className={cn("tv-mono text-caption", up ? "text-tv-green" : "text-tv-red")}>
                  {formatPrice(chg.last, pair)}
                </span>
                <span className={cn("tv-mono w-14 text-right text-caption", up ? "text-tv-green" : "text-tv-red")}>
                  {formatPct(chg.pct)}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <Sparkline values={spark} up={up} />
                <BiasIcon bias={biasStr} />
                <span className="text-micro capitalize" style={{ color: biasColor(biasStr) }}>
                  {biasStr}
                </span>
                {bias?.aligned && <span className="text-micro text-tv-green">aligned</span>}
                {top && (
                  <span
                    className={cn(
                      "tv-mono ml-auto text-micro font-medium",
                      top.direction > 0 ? "text-tv-green" : "text-tv-red"
                    )}
                  >
                    {directionLabel(top.direction)} {Math.abs(Number(top.final_score)).toFixed(0)}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="space-y-1 border-t border-tv-border px-3 py-2 text-micro text-tv-dim">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-tv-red" />
          Act Now ≥70 · ≤6h
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-tv-amber" />
          Watch ≥50 · ≤24h
        </div>
      </div>
    </div>
  );
}

function Sparkline({ values, up }: { values: number[]; up: boolean }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 56;
  const h = 16;
  const d = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} className="shrink-0" aria-hidden>
      <path d={d} fill="none" stroke={up ? "var(--tv-green)" : "var(--tv-red)"} strokeWidth="1.2" />
    </svg>
  );
}

function BiasIcon({ bias }: { bias: string }) {
  if (bias.includes("bull")) return <TrendingUp className="h-3 w-3 text-tv-green" />;
  if (bias.includes("bear")) return <TrendingDown className="h-3 w-3 text-tv-red" />;
  return <Minus className="h-3 w-3 text-tv-muted" />;
}
