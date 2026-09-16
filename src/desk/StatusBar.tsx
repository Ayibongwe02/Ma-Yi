import { useTvTheme } from "./theme";

export default function StatusBar({ pair, tf = "1h" }: { pair: string; tf?: string }) {
  const { theme } = useTvTheme();
  const themeLabel = theme === "black" ? "Black · Premium" : theme === "light" ? "Light" : "Dark";
  return (
    <div className="hidden h-6 shrink-0 items-center gap-3 border-t border-tv-border bg-tv-panel px-3 text-micro text-tv-dim md:flex">
      <span className="text-tv-green">●</span>
      <span>FX · London / NY overlap</span>
      <span className="text-tv-border">|</span>
      <span className="tv-mono">{tf}</span>
      <span className="text-tv-border">|</span>
      <span>{pair.replace("=X", "")}</span>
      <span className="flex-1" />
      <span>Theme {themeLabel}</span>
      <span className="text-tv-border">|</span>
      <span>UTC</span>
      <span className="text-tv-border">|</span>
      <span>Ma-yi Sentinel · Stage 4</span>
    </div>
  );
}
