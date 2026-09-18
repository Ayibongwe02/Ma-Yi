import { useState } from "react";
import type { Order, Signal } from "./types";
import { cn, directionLabel, displayPair, formatTs } from "./utils";
import { ChevronDown, ChevronUp } from "lucide-react";

interface Props {
  logs: string[];
  orders: Order[];
  signals: Signal[];
  collapsed?: boolean;
  onToggle?: () => void;
}

type Tab = "tape" | "orders" | "signals";

export default function BottomTape({ logs, orders, signals, collapsed = false, onToggle }: Props) {
  const [tab, setTab] = useState<Tab>("tape");

  return (
    <footer className="flex h-full min-h-0 shrink-0 flex-col border-t border-tv-border bg-tv-panel">
      <div className="flex h-8 shrink-0 items-center border-b border-tv-border px-2">
        {(
          [
            ["tape", "Engine log"],
            ["signals", "Recent signals"],
            ["orders", "Order blotter"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => {
              setTab(id);
              if (collapsed) onToggle?.();
            }}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-caption font-medium transition-colors duration-150",
              tab === id && !collapsed
                ? "border-tv-blue text-tv-text"
                : "border-transparent text-tv-muted hover:text-tv-text"
            )}
          >
            {label}
          </button>
        ))}
        <div className="flex-1" />
        {onToggle && (
          <button
            onClick={onToggle}
            className="p-1 text-tv-muted hover:text-tv-text"
            aria-label={collapsed ? "Expand tape" : "Collapse tape"}
          >
            {collapsed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="min-h-0 flex-1 overflow-y-auto font-mono text-caption leading-relaxed">
          {tab === "tape" && (
            <div className="space-y-0.5 px-3 py-1">
              {logs.length === 0 ? (
                <div className="py-4 text-center font-sans text-tv-dim">Engine log empty — run a scan</div>
              ) : (
                [...logs].reverse().map((line, i) => (
                  <div
                    key={i}
                    className={cn(
                      "whitespace-pre-wrap break-all",
                      line.toLowerCase().includes("error")
                        ? "text-tv-red"
                        : line.toLowerCase().includes("signal") || line.toLowerCase().includes("fired")
                          ? "text-tv-green-bright"
                          : "text-tv-muted"
                    )}
                  >
                    {line}
                  </div>
                ))
              )}
            </div>
          )}

          {tab === "signals" && (
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-tv-panel-2 text-tv-dim">
                <tr>
                  <th className="px-3 py-1 font-medium">Time</th>
                  <th className="px-2 py-1 font-medium">Pair</th>
                  <th className="px-2 py-1 font-medium">Side</th>
                  <th className="px-2 py-1 font-medium">Pattern</th>
                  <th className="px-2 py-1 text-right font-medium">Score</th>
                  <th className="px-2 py-1 font-medium">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id} className="border-t border-tv-border hover:bg-tv-hover">
                    <td className="px-3 py-1 text-tv-dim">{formatTs(s.bar_time, "MM-dd HH:mm")}</td>
                    <td className="px-2 py-1">{displayPair(s.pair)}</td>
                    <td className={cn("px-2 py-1 font-semibold", s.direction > 0 ? "text-tv-green" : "text-tv-red")}>
                      {directionLabel(s.direction)}
                    </td>
                    <td className="px-2 py-1 text-tv-muted">{s.pattern?.replace(/_/g, " ")}</td>
                    <td
                      className={cn(
                        "px-2 py-1 text-right font-semibold",
                        s.final_score >= 0 ? "text-tv-green" : "text-tv-red"
                      )}
                    >
                      {s.final_score != null ? Number(s.final_score).toFixed(0) : "—"}
                    </td>
                    <td className="px-2 py-1 capitalize text-tv-muted">{s.outcome || "pending"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {tab === "orders" && (
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-tv-panel-2 text-tv-dim">
                <tr>
                  <th className="px-3 py-1 font-medium">Time</th>
                  <th className="px-2 py-1 font-medium">Pair</th>
                  <th className="px-2 py-1 font-medium">Side</th>
                  <th className="px-2 py-1 text-right font-medium">Units</th>
                  <th className="px-2 py-1 font-medium">Status</th>
                  <th className="px-2 py-1 font-medium">Mode</th>
                </tr>
              </thead>
              <tbody>
                {orders.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-center font-sans text-tv-dim">
                      No orders yet (dry-run blotter)
                    </td>
                  </tr>
                ) : (
                  orders.map((o, i) => (
                    <tr key={i} className="border-t border-tv-border hover:bg-tv-hover">
                      <td className="px-3 py-1 text-tv-dim">{formatTs(o.ts, "MM-dd HH:mm:ss")}</td>
                      <td className="px-2 py-1">{displayPair(o.pair)}</td>
                      <td className="px-2 py-1">{o.side}</td>
                      <td className="px-2 py-1 text-right">{o.units ?? "—"}</td>
                      <td className="px-2 py-1">{o.status}</td>
                      <td className="px-2 py-1 text-tv-muted">{o.dry_run !== false ? "dry-run" : "live"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>
      )}
    </footer>
  );
}
