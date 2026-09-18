import {
  Crosshair,
  Minus,
  TrendingUp,
  Layers,
  MousePointer2,
  Trash2,
  Magnet,
} from "lucide-react";
import type { DrawTool } from "./types";
import { cn } from "./utils";

interface Props {
  tool: DrawTool;
  onTool: (t: DrawTool) => void;
  onClear: () => void;
  magnet: boolean;
  onMagnet: () => void;
}

const TOOLS: { id: DrawTool; icon: typeof Crosshair; label: string }[] = [
  { id: "cursor", icon: MousePointer2, label: "Cursor" },
  { id: "hline", icon: Minus, label: "Horizontal line" },
  { id: "trend", icon: TrendingUp, label: "Trend line" },
  { id: "fib", icon: Layers, label: "Fibonacci" },
];

export default function DrawToolbar({ tool, onTool, onClear, magnet, onMagnet }: Props) {
  return (
    <div className="hidden md:flex w-10 shrink-0 flex-col items-center gap-0.5 border-r border-tv-border bg-tv-panel py-1">
      <div className="mb-1 flex h-7 w-7 items-center justify-center text-tv-muted" title="Crosshair always on">
        <Crosshair className="h-3.5 w-3.5" />
      </div>
      {TOOLS.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          title={label}
          onClick={() => onTool(id)}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-sm text-tv-muted transition-colors duration-150 hover:bg-tv-hover hover:text-tv-text",
            tool === id && "bg-tv-blue text-white hover:bg-tv-blue hover:text-white"
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
      <div className="my-1 h-px w-6 bg-tv-border" />
      <button
        title="Magnet (snap)"
        onClick={onMagnet}
        className={cn(
          "flex h-8 w-8 items-center justify-center rounded-sm text-tv-muted transition-colors duration-150 hover:bg-tv-hover hover:text-tv-text",
          magnet && "text-tv-blue"
        )}
      >
        <Magnet className="h-3.5 w-3.5" />
      </button>
      <button
        title="Remove drawings"
        onClick={onClear}
        className="mt-auto mb-1 flex h-8 w-8 items-center justify-center rounded-sm text-tv-muted transition-colors duration-150 hover:bg-tv-hover hover:text-tv-red"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
