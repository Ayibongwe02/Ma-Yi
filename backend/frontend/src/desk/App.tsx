import { useEffect, useState } from "react";
import { BarChart3, List, Radio } from "lucide-react";
import type { MobilePane, Signal } from "./types";
import { cn } from "./utils";
import { ThemeProvider } from "./theme";
import { useDeskStore } from "./store";
import TopBar from "./TopBar";
import WatchlistPanel from "./WatchlistPanel";
import ChartPanel from "./ChartPanel";
import TriagePanel from "./TriagePanel";
import SignalDeepDive from "./SignalDeepDive";
import BottomTape from "./BottomTape";
import StatusBar from "./StatusBar";

function DeskShell() {
  const live = useDeskStore((s) => s.live);
  const scanning = useDeskStore((s) => s.scanning);
  const autoScanning = useDeskStore((s) => s.autoScanning);
  const killEnabled = useDeskStore((s) => s.killEnabled);
  const killBusy = useDeskStore((s) => s.killBusy);
  const ml = useDeskStore((s) => s.ml);
  const logs = useDeskStore((s) => s.logs);
  const orders = useDeskStore((s) => s.orders);
  const scan = useDeskStore((s) => s.scan);
  const toggleKill = useDeskStore((s) => s.toggleKill);
  const tick = useDeskStore((s) => s.tick);
  const hydrate = useDeskStore((s) => s.hydrate);
  const refreshFromBackend = useDeskStore((s) => s.refreshFromBackend);
  const backendOnline = useDeskStore((s) => s.backendOnline);
  const anyLoading = scanning || autoScanning;

  const [selected, setSelected] = useState<Signal | null>(null);
  const [activePair, setActivePair] = useState("EURUSD=X");
  const [view, setView] = useState<"desk" | "deep">("desk");
  const [mobilePane, setMobilePane] = useState<MobilePane>("chart");
  const [tapeOpen, setTapeOpen] = useState(true);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    const id = window.setInterval(tick, 2200);
    return () => window.clearInterval(id);
  }, [tick]);

  useEffect(() => {
    if (backendOnline !== true) return;
    const id = window.setInterval(() => {
      void refreshFromBackend();
    }, 15_000);
    return () => window.clearInterval(id);
  }, [backendOnline, refreshFromBackend]);

  const actNow = live.act_now;
  const watch = live.watch;
  const pairBias = live.pair_bias ?? [];
  const stats = live.stats;

  useEffect(() => {
    if (!selected) {
      const first = actNow[0] ?? watch[0] ?? null;
      if (first) {
        setSelected(first);
        setActivePair(first.pair);
      }
    }
  }, [actNow, watch, selected]);

  const handleSelect = (s: Signal) => {
    setSelected(s);
    setActivePair(s.pair);
    setView("desk");
  };

  const handlePair = (pair: string) => {
    setActivePair(pair);
    const match = [...actNow, ...watch].find((s) => s.pair === pair);
    if (match) setSelected(match);
  };

  const briefParts = [
    `${actNow.length} act-now`,
    `${watch.length} watch`,
    backendOnline ? "backend live" : "demo mode",
  ];
  const brief = briefParts.join(" · ");

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-tv-bg text-tv-text">
      <TopBar
        scanning={scanning}
        autoScanning={autoScanning}
        onScan={() => void scan()}
        killEnabled={killEnabled}
        killBusy={killBusy}
        onToggleKill={toggleKill}
        ml={ml}
        stats={stats}
        view={view}
        onViewChange={setView}
        timeframe="1h"
      />

      <div className="hidden items-center gap-2 border-b border-tv-border bg-tv-panel px-3 py-1.5 text-xs text-tv-muted md:flex">
        <span className="text-micro font-semibold uppercase tracking-wider text-tv-dim">Brief</span>
        <span className="truncate">{brief}</span>
        {ml?.paused && <span className="ml-auto text-tv-amber">ML paused · explore</span>}
      </div>

      {/* ---- Mobile panes ---- */}
      <div className="flex min-h-0 flex-1 md:hidden">
        <aside
          className={cn(
            "w-full flex-col border-r border-tv-border bg-tv-panel",
            mobilePane === "watch" ? "flex" : "hidden"
          )}
        >
          <WatchlistPanel
            activePair={activePair}
            onSelectPair={handlePair}
            pairBias={pairBias}
            actNow={actNow}
            watch={watch}
          />
        </aside>

        <main className={cn("min-w-0 flex-1 flex-col", mobilePane === "chart" ? "flex" : "hidden")}>
          {view === "desk" ? (
            <ChartPanel
              pair={activePair}
              selected={selected}
              actNow={actNow}
              watch={watch}
              onSelect={handleSelect}
            />
          ) : (
            <SignalDeepDive signal={selected} onBack={() => setView("desk")} pairBias={pairBias} ml={ml} />
          )}
        </main>

        <aside
          className={cn("w-full flex-col bg-tv-panel", mobilePane === "triage" ? "flex" : "hidden")}
        >
          <TriagePanel
            actNow={actNow}
            watch={watch}
            selectedId={selected?.id}
            onSelect={handleSelect}
            loading={anyLoading}
          />
        </aside>
      </div>

      {/* ---- Desktop: fixed readable grid ---- */}
      <div
        className="hidden min-h-0 flex-1 md:grid"
        style={{
          gridTemplateColumns: "220px minmax(0, 1fr) 280px",
          gridTemplateRows: tapeOpen ? "minmax(0, 1fr) 180px" : "minmax(0, 1fr) 32px",
        }}
      >
        {/* Watchlist */}
        <aside className="min-h-0 overflow-hidden border-r border-tv-border bg-tv-panel" style={{ gridRow: "1 / -1" }}>
          <WatchlistPanel
            activePair={activePair}
            onSelectPair={handlePair}
            pairBias={pairBias}
            actNow={actNow}
            watch={watch}
          />
        </aside>

        {/* Chart / Deep dive */}
        <main className="min-h-0 min-w-0 overflow-hidden">
          {view === "desk" ? (
            <ChartPanel
              pair={activePair}
              selected={selected}
              actNow={actNow}
              watch={watch}
              onSelect={handleSelect}
            />
          ) : (
            <SignalDeepDive signal={selected} onBack={() => setView("desk")} pairBias={pairBias} ml={ml} />
          )}
        </main>

        {/* Triage */}
        <aside className="min-h-0 overflow-hidden border-l border-tv-border bg-tv-panel">
          <TriagePanel
            actNow={actNow}
            watch={watch}
            selectedId={selected?.id}
            onSelect={handleSelect}
            loading={anyLoading}
          />
        </aside>

        {/* Bottom tape spans chart + triage columns */}
        <div className="min-h-0 overflow-hidden border-t border-tv-border" style={{ gridColumn: "2 / -1" }}>
          <BottomTape
            logs={logs}
            orders={orders}
            signals={[...actNow, ...watch].slice(0, 12)}
            collapsed={!tapeOpen}
            onToggle={() => setTapeOpen((v) => !v)}
          />
        </div>
      </div>

      <StatusBar pair={activePair} />

      <nav className="grid shrink-0 grid-cols-3 border-t border-tv-border bg-tv-panel pb-[env(safe-area-inset-bottom)] md:hidden">
        {(
          [
            ["watch", "Pairs", Radio],
            ["chart", "Chart", BarChart3],
            ["triage", "Triage", List],
          ] as const
        ).map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setMobilePane(id)}
            className={cn(
              "flex min-h-12 flex-col items-center justify-center gap-0.5 py-2.5 text-micro font-medium",
              mobilePane === id ? "text-tv-text" : "text-tv-muted"
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </nav>
    </div>
  );
}

export default function DeskApp() {
  return (
    <ThemeProvider>
      <DeskShell />
    </ThemeProvider>
  );
}
