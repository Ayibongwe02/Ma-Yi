import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { TvTheme } from "./types";

const KEY = "mayi-tv-theme";

const ThemeCtx = createContext<{
  theme: TvTheme;
  setTheme: (t: TvTheme) => void;
}>({ theme: "dark", setTheme: () => {} });

function applyTheme(t: TvTheme) {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-tv-theme", t);
  document.documentElement.style.colorScheme = t === "light" ? "light" : "dark";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<TvTheme>("dark");

  useEffect(() => {
    const stored = localStorage.getItem(KEY) as TvTheme | null;
    const next = stored === "black" || stored === "light" || stored === "dark" ? stored : "dark";
    setThemeState(next);
    applyTheme(next);
  }, []);

  const setTheme = (t: TvTheme) => {
    setThemeState(t);
    localStorage.setItem(KEY, t);
    applyTheme(t);
  };

  const value = useMemo(() => ({ theme, setTheme }), [theme]);
  return (
    <ThemeCtx.Provider value={value}>
      <div className="h-full">{children}</div>
    </ThemeCtx.Provider>
  );
}

export function useTvTheme() {
  return useContext(ThemeCtx);
}
