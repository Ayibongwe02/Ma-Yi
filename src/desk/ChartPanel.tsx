import { useEffect, useMemo, useState } from "react";
import type { Candle, ChartDrawing, DrawTool, Signal } from "./types";
import {
  cn,
  confidenceLabel,
  displayPair,
  formatAge,
  formatPct,
  formatPips,
  formatPrice,
  patternPretty,
  scoreColor,
} from "./utils";
import { getCandles as getDemoCandles, sessionChange } from "./demo-data";
import { fetchCandles } from "./api";
import { useDeskStore } from "./store";
import ChartCanvas from "./ChartCanvas";
import DrawToolbar from "./DrawToolbar";

const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;

interface Props {
  pair: string;
  selected: Signal | null;
  actNow: Signal[];
  watch: Signal[];
  onSelect: (s: Signal) => void;
}

export default function ChartPanel({ pair, selected, actNow, watch }: Props) {
  const feed = useDeskStore((s) => s.feed);
  const [tf, setTf] = useState<string>("1h");
  const [showMulti, setShowMulti] = useState(false);
  const [showLevels, setShowLevels] = useState(true);
  const [tool, setTool] = useState<DrawTool>("cursor");
  const [drawings, setDrawings] = useState<ChartDrawing[]>([]);
  const [magnet, setMagnet] = useState(true);
  const [hoverCandle, setHoverCandle] = useState<Candle | null>(null);

  const backendOnline = useDeskStore((s) => s.backendOnline);
  const [liveCandles, setLiveCandles] = useState<Candle[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (backendOnline !== true) {
      setLiveCandles(null);
      return;
    }
    fetchCandles(pair, tf, 140)
      .then((rows) => {
        if (!cancelled && rows.length) setLiveCandles(rows);
      })
      .catch(() => {
        if (!cancelled) setLiveCandles(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pair, tf, feed, backendOnline]);

  const candles = useMemo(
    () => (liveCandles && liveCandles.length ? liveCandles : getDemoCandles(pair, tf, 140)),
    [pair, tf, feed, liveCandles]
  );
  const allSignals = [...actNow, ...watch];
  const focus =
    selected && selected.pair === pair ? selected : allSignals.find((s) => s.pair === pair) || null;

  const chg = sessionChange(pair, tf);
  const last = candles[candles.length - 1];
  const up = chg.pct >= 0;
  const ohlc = hoverCandle ?? last;

  return (
    <div className="flex h-full min-h-0 flex-col bg-tv-bg">
      <div className="flex min-h-10 shrink-0 items-center gap-2 border-b border-tv-border bg-tv-panel px-2 py-1">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <h1 className="text-base font-semibold tracking-tight">{displayPair(pair)}</h1>
            {last && (
              <span className={cn("tv-mono text-base font-semibold", up ? "text-tv-green" : "text-tv-red")}>
                {formatPrice(last.c, pair)}
              </span>
            )}
            <span className={cn("tv-mono text-caption font-medium", up ? "text-tv-green" : "text-tv-red")}>
              {up ? "+" : ""}
              {formatPrice(chg.last - chg.prev, pair)} ({formatPct(chg.pct)})
            </span>
            {focus && (
              <span
                className={cn(
                  "tv-mono rounded-sm px-1.5 py-0.5 text-micro font-semibold",
                  focus.direction > 0
                    ? "bg-[color-mix(in_oklab,var(--tv-green)_18%,transparent)] text-tv-green"
                    : "bg-[color-mix(in_oklab,var(--tv-red)_18%,transparent)] text-tv-red"
                )}
              >
                {focus.direction > 0 ? "BUY" : "SELL"} {Math.abs(focus.final_score).toFixed(0)}
              </span>
            )}
          </div>
          {ohlc && (
            <div className="hidden gap-2 tv-mono text-micro text-tv-muted sm:flex">
              <span>
                O <span className="text-tv-text">{formatPrice(ohlc.o, pair)}</span>
              </span>
              <span>
                H <span className="text-tv-green">{formatPrice(ohlc.h, pair)}</span>
              </span>
              <span>
                L <span className="text-tv-red">{formatPrice(ohlc.l, pair)}</span>
              </span>
              <span>
                C <span className="text-tv-text">{formatPrice(ohlc.c, pair)}</span>
              </span>
              <span>
                Vol <span className="text-tv-text">{ohlc.v}</span>
              </span>
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <div className="flex rounded-sm border border-tv-border bg-tv-panel-2 p-0.5">
            {TIMEFRAMES.map((t) => (
              <button
                key={t}
                onClick={() => {
                  setTf(t);
                  setShowMulti(false);
                }}
                className={cn(
                  "rounded-sm px-2 py-0.5 text-caption font-medium transition-colors duration-150",
                  tf === t && !showMulti ? "bg-tv-blue text-white" : "text-tv-muted hover:text-tv-text"
                )}
              >
                {t}
              </button>
            ))}
          </div>
          <button
            onClick={() => setShowMulti((v) => !v)}
            className={cn(
              "rounded-sm border px-2 py-1 text-caption transition-colors duration-150",
              showMulti ? "border-tv-blue bg-tv-blue text-white" : "border-tv-border text-tv-muted hover:text-tv-text"
            )}
          >
            Multi-TF
          </button>
          <button
            onClick={() => setShowLevels((v) => !v)}
            className={cn(
              "rounded-sm border px-2 py-1 text-caption transition-colors duration-150",
              showLevels
                ? "border-tv-blue bg-[color-mix(in_oklab,var(--tv-blue)_18%,transparent)] text-tv-blue"
                : "border-tv-border text-tv-muted"
            )}
          >
            Levels
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <DrawToolbar
          tool={tool}
          onTool={(t) => {
            setTool(t);
            setShowMulti(false);
          }}
          onClear={() => setDrawings([])}
          magnet={magnet}
          onMagnet={() => setMagnet((v) => !v)}
        />
        <div className="min-h-0 min-w-0 flex-1 p-0">
          {showMulti ? (
            <div className="grid h-full grid-cols-1 grid-rows-4 gap-px bg-tv-border sm:grid-cols-2 sm:grid-rows-2">
              {TIMEFRAMES.map((t) => (
                <MiniChart key={t} pair={pair} timeframe={t} focus={focus} showLevels={showLevels} />
              ))}
            </div>
          ) : (
            <ChartCanvas
              candles={candles}
              pair={pair}
              focus={focus}
              showLevels={showLevels}
              tool={tool}
              drawings={drawings}
              onDraw={(d) => setDrawings((prev) => [...prev, d])}
              magnet={magnet}
              onHover={setHoverCandle}
            />
          )}
        </div>
      </div>

      {focus && (
        <div className="shrink-0 border-t border-tv-border bg-tv-panel px-2 py-1.5">
          <div className="grid grid-cols-5 gap-1.5">
            <LevelCard label="Entry" value={formatPrice(focus.entry, focus.pair)} accent="var(--tv-blue)" />
            <LevelCard
              label="Stop"
              value={formatPrice(focus.sl, focus.pair)}
              accent="var(--tv-red)"
              sub={formatPips(focus.risk, focus.pair)}
            />
            <LevelCard
              label="Target"
              value={formatPrice(focus.tp, focus.pair)}
              accent="var(--tv-green)"
              sub={`R:R ${focus.rr?.toFixed(1) ?? "—"}`}
            />
            <LevelCard
              label="Score"
              value={`${focus.final_score > 0 ? "+" : ""}${focus.final_score.toFixed(0)}`}
              accent={scoreColor(focus.final_score)}
              sub={confidenceLabel(focus.final_score)}
            />
            <LevelCard
              label="Context"
              value={focus.volatility || "—"}
              accent="var(--tv-text)"
              sub={`SR ${((focus.sr_score || 0) * 100).toFixed(0)}%`}
            />
          </div>
          {focus.pattern && (
            <div className="mt-1 px-1 text-caption text-tv-muted">
              {patternPretty(focus.pattern)} · {formatAge(focus.bar_time)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function LevelCard({
  label,
  value,
  accent,
  sub,
}: {
  label: string;
  value: string;
  accent: string;
  sub?: string;
}) {
  return (
    <div className="rounded-sm border border-tv-border bg-tv-panel-2 px-2 py-1.5">
      <div className="text-micro uppercase tracking-wider text-tv-muted">{label}</div>
      <div className="tv-mono text-desk font-semibold" style={{ color: accent }}>
        {value}
      </div>
      {sub && <div className="text-micro text-tv-dim">{sub}</div>}
    </div>
  );
}

function MiniChart({
  pair,
  timeframe,
  focus,
  showLevels,
}: {
  pair: string;
  timeframe: string;
  focus: Signal | null;
  showLevels: boolean;
}) {
  const feed = useDeskStore((s) => s.feed);
  const candles = useMemo(() => getDemoCandles(pair, timeframe, 60), [pair, timeframe, feed]);
  return (
    <div className="flex min-h-0 flex-col bg-tv-bg">
      <div className="flex items-center justify-between border-b border-tv-border px-2 py-0.5 text-micro font-semibold uppercase tracking-wider text-tv-muted">
        <span>{timeframe}</span>
        <span className="tv-mono normal-case text-tv-dim">{candles.length} bars</span>
      </div>
      <div className="min-h-0 flex-1">
        <ChartCanvas
          candles={candles}
          pair={pair}
          focus={focus}
          showLevels={showLevels}
          tool="cursor"
          drawings={[]}
          onDraw={() => {}}
        />
      </div>
    </div>
  );
}
