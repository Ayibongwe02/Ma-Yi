import {
  Radio,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Brain,
  LayoutDashboard,
  Crosshair,
} from "lucide-react";
import type { MlStatus, Stats, TvTheme } from "./types";
import { cn, formatWinRate } from "./utils";
import { useTvTheme } from "./theme";

interface Props {
  scanning: boolean;
  onScan: () => void;
  killEnabled: boolean;
  killBusy?: boolean;
  onToggleKill: () => void;
  ml?: MlStatus;
  stats?: Stats;
  view: "desk" | "deep";
  onViewChange: (v: "desk" | "deep") => void;
}

const THEMES: { id: TvTheme; label: string }[] = [
  { id: "dark", label: "Dark" },
  { id: "black", label: "Black" },
  { id: "light", label: "Light" },
];

export default function TopBar({
  scanning,
  onScan,
  killEnabled,
  killBusy,
  onToggleKill,
  ml,
  stats,
  view,
  onViewChange,
}: Props) {
  const { theme, setTheme } = useTvTheme();
  const health = String((ml?.health as any)?.state ?? ml?.health ?? "unknown").toLowerCase();
  const healthColor =
    health === "healthy"
      ? "text-tv-green"
      : health.includes("overfit") || health === "paused"
        ? "text-tv-amber"
        : "text-tv-muted";

  return (
    <header className="flex min-h-11 shrink-0 items-center gap-2 border-b border-tv-border bg-tv-panel px-2 py-1.5 sm:px-3">
      <div className="flex items-center gap-2 border-r border-tv-border pr-2 sm:pr-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-sm bg-tv-blue">
          <Radio className="h-4 w-4 text-white" />
        </div>
        <div className="hidden leading-tight sm:block">
          <div className="flex items-center gap-1.5">
            <span className="text-desk font-semibold tracking-tight">Ma-yi</span>
            <span className="rounded-sm bg-tv-blue px-1 py-px text-micro font-semibold uppercase tracking-wider text-white">
              Sentinel
            </span>
          </div>
          <div className="-mt-0.5 text-micro text-tv-muted">Live Command · Stage 4</div>
        </div>
      </div>

      <div className="flex items-center gap-0.5 rounded-sm border border-tv-border bg-tv-panel-2 p-0.5">
        <button
          onClick={() => onViewChange("desk")}
          className={cn(
            "flex min-h-7 items-center gap-1.5 rounded-sm px-2 py-1 text-caption font-medium transition-colors duration-150 sm:px-2.5",
            view === "desk" ? "bg-tv-blue text-white" : "text-tv-muted hover:text-tv-text"
          )}
        >
          <LayoutDashboard className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Desk</span>
        </button>
        <button
          onClick={() => onViewChange("deep")}
          className={cn(
            "flex min-h-7 items-center gap-1.5 rounded-sm px-2 py-1 text-caption font-medium transition-colors duration-150 sm:px-2.5",
            view === "deep" ? "bg-tv-blue text-white" : "text-tv-muted hover:text-tv-text"
          )}
        >
          <Crosshair className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Deep Dive</span>
        </button>
      </div>

      <div className="flex items-center gap-1.5 text-caption text-tv-muted">
        <span className="live-dot h-1.5 w-1.5 rounded-full bg-tv-green" />
        <span className="hidden sm:inline">LIVE</span>
        <span className="hidden text-tv-dim lg:inline">· replay engine</span>
      </div>

      <div className="flex-1" />

      {stats && (
        <div className="tv-mono hidden items-center gap-2 text-caption lg:flex">
          <span className="text-tv-muted">
            W/L <span className="text-tv-green">{stats.wins}</span>/
            <span className="text-tv-red">{stats.losses}</span>
          </span>
          <span className="text-tv-border">|</span>
          <span>
            WR <span className="text-tv-text">{formatWinRate(stats.win_rate)}</span>
          </span>
          {stats.expectancy != null && (
            <>
              <span className="text-tv-border">|</span>
              <span>
                E[R]{" "}
                <span className={stats.expectancy >= 0 ? "text-tv-green" : "text-tv-red"}>
                  {stats.expectancy.toFixed(2)}
                </span>
              </span>
            </>
          )}
        </div>
      )}

      <div
        className={cn(
          "hidden items-center gap-1.5 rounded-sm border border-tv-border px-2 py-1 text-caption sm:flex",
          healthColor
        )}
        title={ml?.message || "ML model status"}
      >
        <Brain className="h-3.5 w-3.5" />
        <span className="capitalize">{typeof ml?.health === "object" ? health : (ml?.health ?? "—")}</span>
        {ml?.n_samples != null && <span className="tv-mono text-tv-dim">n={ml.n_samples}</span>}
        {ml?.paused && <span className="text-tv-amber">paused</span>}
      </div>

      <div className="flex rounded-sm border border-tv-border bg-tv-panel-2 p-0.5 md:hidden">
        <button
          onClick={() => {
            const order: TvTheme[] = ["dark", "black", "light"];
            const i = order.indexOf(theme);
            setTheme(order[(i + 1) % order.length]);
          }}
          className="px-2 py-0.5 text-micro font-medium uppercase tracking-wide text-tv-text"
          title="Cycle theme"
        >
          {theme}
        </button>
      </div>

      <div className="hidden rounded-sm border border-tv-border bg-tv-panel-2 p-0.5 md:flex">
        {THEMES.map((t) => (
          <button
            key={t.id}
            onClick={() => setTheme(t.id)}
            title={`TradingView ${t.label}${t.id === "black" ? " (Premium)" : ""}`}
            className={cn(
              "rounded-sm px-2 py-0.5 text-micro font-medium uppercase tracking-wide transition-colors duration-150",
              theme === t.id ? "bg-tv-blue text-white" : "text-tv-muted hover:text-tv-text"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <button
        onClick={onToggleKill}
        disabled={killBusy}
        className={cn(
          "flex min-h-8 items-center gap-1.5 rounded-sm border px-2 py-1.5 text-caption font-medium transition-colors duration-150 sm:px-2.5",
          killEnabled
            ? "border-tv-red bg-[color-mix(in_oklab,var(--tv-red)_15%,transparent)] text-tv-red"
            : "border-tv-green bg-[color-mix(in_oklab,var(--tv-green)_12%,transparent)] text-tv-green"
        )}
      >
        {killEnabled ? (
          <>
            <ShieldAlert className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">KILL ON</span>
          </>
        ) : (
          <>
            <ShieldCheck className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">ARMED</span>
          </>
        )}
      </button>

      <button
        onClick={onScan}
        disabled={scanning}
        className="flex min-h-8 items-center gap-1.5 rounded-sm bg-tv-blue px-3 py-1.5 text-xs font-semibold text-white transition-colors duration-150 hover:bg-tv-blue-dim disabled:opacity-60"
      >
        <RefreshCw className={cn("h-3.5 w-3.5", scanning && "animate-spin")} />
        <span className="hidden sm:inline">{scanning ? "Scanning…" : "Scan now"}</span>
      </button>
    </header>
  );
}
