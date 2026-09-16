import { useEffect, useMemo, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
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
  const killEnabled = useDeskStore((s) => s.killEnabled);
  const killBusy = useDeskStore((s) => s.killBusy);
  const ml = useDeskStore((s) => s.ml);
  const logs = useDeskStore((s) => s.logs);
  const orders = useDeskStore((s) => s.orders);
  const feed = useDeskStore((s) => s.feed);
  const scan = useDeskStore((s) => s.scan);
  const toggleKill = useDeskStore((s) => s.toggleKill);
  const tick = useDeskStore((s) => s.tick);
  const hydrate = useDeskStore((s) => s.hydrate);

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

  const actNow = live.act_now;
  const watch = live.watch;
  const pairBias = live.pair_bias ?? [];
  const stats = live.stats;
  const book = live.bias;

  useEffect(() => {
    if (!selected) {
      const first = actNow[0] ?? watch[0] ?? null;
      if (first) {
        setSelected(first);
        setActivePair(first.pair);
      }
    }
  }, [actNow, watch, selected]);

  const brief = useMemo(() => {
    const aligned = pairBias.filter((b) => b.aligned).length;
    const net = book?.net ?? 0;
    const lean = net > 1 ? "book leans long" : net < -1 ? "book leans short" : "book is mixed";
    return `${actNow.length} act-now · ${watch.length} watch · ${aligned}/${pairBias.length || 5} TF-aligned · ${lean}`;
  }, [actNow.length, watch.length, pairBias, book]);

  const handleSelect = (s: Signal) => {
    setSelected(s);
    setActivePair(s.pair);
    setView("deep");
    setMobilePane("chart");
  };

  const handlePair = (p: string) => {
    setActivePair(p);
    const hit = [...actNow, ...watch].find((s) => s.pair === p);
    if (hit) setSelected(hit);
    setView("desk");
    setMobilePane("chart");
  };

  return (
    <div className="flex h-dvh flex-col bg-tv-bg text-tv-text" data-feed={feed}>
      <TopBar
        scanning={scanning}
        onScan={() => void scan()}
        killEnabled={killEnabled}
        killBusy={killBusy}
        onToggleKill={toggleKill}
        ml={ml}
        stats={stats}
        view={view}
        onViewChange={setView}
      />

      <div className="hidden items-center gap-2 border-b border-tv-border bg-tv-panel px-3 py-1.5 text-xs text-tv-muted md:flex">
        <span className="text-micro font-semibold uppercase tracking-wider text-tv-dim">Brief</span>
        <span className="truncate">{brief}</span>
        {ml.paused && <span className="ml-auto text-tv-amber">ML paused · explore</span>}
      </div>

      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            "w-full shrink-0 flex-col border-r border-tv-border bg-tv-panel md:hidden",
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

        <main className={cn("min-w-0 flex-1 flex-col md:hidden", mobilePane === "chart" ? "flex" : "hidden")}>
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
          className={cn("w-full shrink-0 flex-col bg-tv-panel md:hidden", mobilePane === "triage" ? "flex" : "hidden")}
        >
          <TriagePanel
            actNow={actNow}
            watch={watch}
            selectedId={selected?.id}
            onSelect={handleSelect}
            loading={scanning}
          />
        </aside>

        <div className="hidden min-h-0 min-w-0 flex-1 md:flex">
          <Group orientation="horizontal" className="h-full w-full" defaultLayout={{ watch: 18, chart: 54, triage: 28 }}>
            <Panel id="watch" minSize="12%" maxSize="32%" className="min-h-0">
              <WatchlistPanel
                activePair={activePair}
                onSelectPair={handlePair}
                pairBias={pairBias}
                actNow={actNow}
                watch={watch}
              />
            </Panel>
            <Separator className="tv-resize w-1" />
            <Panel id="chart" minSize="36%" className="min-h-0">
              <Group orientation="vertical" className="h-full w-full" defaultLayout={{ main: 78, tape: 22 }}>
                <Panel id="main" minSize="40%" className="min-h-0">
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
                </Panel>
                <Separator className="tv-resize h-1" />
                <Panel id="tape" minSize="8%" maxSize="40%" collapsible collapsedSize={32} className="min-h-0">
                  <BottomTape
                    logs={logs}
                    orders={orders}
                    signals={[...actNow, ...watch].slice(0, 12)}
                    collapsed={!tapeOpen}
                    onToggle={() => setTapeOpen((v) => !v)}
                  />
                </Panel>
              </Group>
            </Panel>
            <Separator className="tv-resize w-1" />
            <Panel id="triage" minSize="18%" maxSize="40%" className="min-h-0">
              <TriagePanel
                actNow={actNow}
                watch={watch}
                selectedId={selected?.id}
                onSelect={handleSelect}
                loading={scanning}
              />
            </Panel>
          </Group>
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
