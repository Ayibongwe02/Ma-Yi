import { useEffect, useState } from "react";
import { useTvTheme } from "./theme";
import { useDeskStore } from "./store";
import { cn, formatCountdown, secondsToNextClose } from "./utils";

export default function StatusBar({ pair, tf = "1h" }: { pair: string; tf?: string }) {
  const { theme } = useTvTheme();
  const themeLabel = theme === "black" ? "Black · Premium" : theme === "light" ? "Light" : "Dark";
  const ml = useDeskStore((s) => s.ml);
  const scanning = useDeskStore((s) => s.scanning);
  const autoScanning = useDeskStore((s) => s.autoScanning);
  const lastUpdated = useDeskStore((s) => s.lastUpdated);
  const usingDemoData = useDeskStore((s) => s.usingDemoData);
  const [secsLeft, setSecsLeft] = useState(() => secondsToNextClose(tf));

  useEffect(() => {
    setSecsLeft(secondsToNextClose(tf));
    const id = window.setInterval(() => setSecsLeft(secondsToNextClose(tf)), 1000);
    return () => window.clearInterval(id);
  }, [tf]);

  const health = String((ml?.health as any)?.state ?? ml?.health ?? "").toLowerCase();
  const loading = scanning || autoScanning;

  // Data freshness. Re-rendered by the 1s countdown interval above, so the
  // age ticks up on its own instead of sitting at whatever it was on mount.
  const ageSec = lastUpdated == null ? null : Math.max(0, Math.round((Date.now() - lastUpdated) / 1000));
  const stale = ageSec != null && ageSec > 90;
  const freshnessLabel = usingDemoData
    ? "demo feed · not live data"
    : ageSec == null
      ? "awaiting first update"
      : ageSec < 60
        ? `updated ${ageSec}s ago`
        : `updated ${Math.floor(ageSec / 60)}m ago`;

  return (
    <div className="hidden h-6 shrink-0 items-center gap-3 border-t border-tv-border bg-tv-panel px-3 text-micro text-tv-dim md:flex">
      <span className={cn(loading ? "text-tv-blue animate-pulse" : "text-tv-green")}>●</span>
      <span>{loading ? (autoScanning ? "Auto-scan running…" : "Scanning…") : "FX · London / NY overlap"}</span>
      <span className="text-tv-border">|</span>
      <span className="tv-mono">{tf}</span>
      <span className="text-tv-border">|</span>
      <span className="tv-mono tabular-nums text-tv-muted" title="Seconds until next closed candle (UTC)">
        close in {formatCountdown(secsLeft)}
      </span>
      <span className="text-tv-border">|</span>
      <span>{pair.replace("=X", "")}</span>
      <span className="text-tv-border">|</span>
      <span
        className={cn(usingDemoData || stale ? "text-tv-amber" : "text-tv-muted")}
        title={
          usingDemoData
            ? "Backend unreachable — showing the synthetic demo feed, not market data."
            : "Time since the last successful backend refresh."
        }
      >
        {freshnessLabel}
      </span>
      {health && (
        <>
          <span className="text-tv-border">|</span>
          <span className="text-tv-muted" title={ml?.message || "ML health state"}>
            ml {health}
          </span>
        </>
      )}
      <span className="flex-1" />
      <span>Theme {themeLabel}</span>
      <span className="text-tv-border">|</span>
      <span>UTC</span>
      <span className="text-tv-border">|</span>
      <span>Ma-yi Sentinel · Stage 4</span>
    </div>
  );
}
