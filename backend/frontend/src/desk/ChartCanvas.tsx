import { useCallback, useEffect, useRef, useState } from "react";
import type { Candle, ChartDrawing, DrawTool, Signal } from "./types";
import { displayPair, formatPrice, priceDigits } from "./utils";

interface Props {
  candles: Candle[];
  pair: string;
  focus: Signal | null;
  showLevels: boolean;
  tool: DrawTool;
  drawings: ChartDrawing[];
  onDraw: (d: ChartDrawing) => void;
  magnet?: boolean;
  onHover?: (c: Candle | null) => void;
}

interface Hover {
  i: number;
  x: number;
  y: number;
  price: number;
  c: Candle;
}

function cssVar(el: HTMLElement, name: string, fallback: string) {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v || fallback;
}

export default function ChartCanvas({
  candles,
  pair,
  focus,
  showLevels,
  tool,
  drawings,
  onDraw,
  magnet = true,
  onHover,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [draft, setDraft] = useState<{ i: number; p: number } | null>(null);
  const layoutRef = useRef({
    plotL: 8,
    plotR: 0,
    plotT: 8,
    plotB: 0,
    volT: 0,
    volB: 0,
    hi: 1,
    lo: 0,
    n: 0,
    w: 0,
    h: 0,
  });

  const pending = useRef<{ i: number; p: number } | null>(null);
  pending.current = draft;

  const toX = (i: number, n: number, plotL: number, plotW: number) =>
    plotL + ((i + 0.5) / n) * plotW;
  const toY = (p: number, hi: number, lo: number, plotT: number, plotH: number) =>
    plotT + ((hi - p) / (hi - lo || 1)) * plotH;
  const fromX = (x: number, n: number, plotL: number, plotW: number) => {
    const i = Math.floor(((x - plotL) / plotW) * n);
    return Math.max(0, Math.min(n - 1, i));
  };
  const fromY = (y: number, hi: number, lo: number, plotT: number, plotH: number) =>
    hi - ((y - plotT) / plotH) * (hi - lo);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (w < 8 || h < 8) return;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const bg = cssVar(wrap, "--tv-bg", "#131722");
    const grid = cssVar(wrap, "--tv-grid", "#1e222d");
    const border = cssVar(wrap, "--tv-border", "#363a45");
    const muted = cssVar(wrap, "--tv-text-dim", "#6a6e78");
    const green = cssVar(wrap, "--tv-green", "#089981");
    const red = cssVar(wrap, "--tv-red", "#f23645");
    const blue = cssVar(wrap, "--tv-blue", "#2962ff");
    const volUp = cssVar(wrap, "--tv-volume-up", "rgba(8,153,129,0.45)");
    const volDn = cssVar(wrap, "--tv-volume-dn", "rgba(242,54,69,0.45)");
    const watermark = cssVar(wrap, "--tv-watermark", "rgba(209,212,220,0.06)");
    const cross = cssVar(wrap, "--tv-cross", "#9598a1");

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    const scaleW = 68;
    const timeH = 22;
    const plotL = 6;
    const plotR = w - scaleW;
    const plotT = 6;
    const volH = Math.max(36, h * 0.16);
    const sep = 6;
    const plotB = h - timeH - volH - sep;
    const volT = plotB + sep;
    const volB = h - timeH;
    const plotW = plotR - plotL;
    const plotH = plotB - plotT;
    const n = candles.length;

    let hi = -Infinity;
    let lo = Infinity;
    let maxV = 1;
    for (const c of candles) {
      const h = Number(c.h), l = Number(c.l), v = Number(c.v);
      if (Number.isFinite(h)) hi = Math.max(hi, h);
      if (Number.isFinite(l)) lo = Math.min(lo, l);
      if (Number.isFinite(v)) maxV = Math.max(maxV, v);
    }
    if (!Number.isFinite(hi) || !Number.isFinite(lo)) {
      hi = 1; lo = 0;
    }
    if (focus && showLevels) {
      const lv = [focus.sl, focus.tp, focus.entry].map(Number).filter(Number.isFinite);
      if (lv.length) { hi = Math.max(hi, ...lv); lo = Math.min(lo, ...lv); }
    }
    for (const d of drawings) {
      hi = Math.max(hi, d.p1, d.p2 ?? d.p1);
      lo = Math.min(lo, d.p1, d.p2 ?? d.p1);
    }
    const pad = (hi - lo) * 0.06 || 0.0004;
    hi += pad;
    lo -= pad;

    layoutRef.current = { plotL, plotR, plotT, plotB, volT, volB, hi, lo, n, w, h };

    const yOf = (p: number) => toY(p, hi, lo, plotT, plotH);
    const xOf = (i: number) => toX(i, n, plotL, plotW);

    // watermark
    ctx.save();
    ctx.fillStyle = watermark;
    ctx.font = "600 64px IBM Plex Sans, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(displayPair(pair), plotL + plotW / 2, plotT + plotH / 2);
    ctx.restore();

    // grid + price labels
    const digits = priceDigits(pair);
    const steps = 8;
    ctx.font = "10px IBM Plex Mono, monospace";
    ctx.textBaseline = "middle";
    for (let s = 0; s <= steps; s++) {
      const p = lo + ((hi - lo) * s) / steps;
      const y = yOf(p);
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plotL, y);
      ctx.lineTo(plotR, y);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.textAlign = "left";
      ctx.fillText(Number(p).toFixed(digits), plotR + 6, y);
    }

    // session markers every ~24 bars
    const stride = Math.max(8, Math.round(n / 6));
    ctx.strokeStyle = border;
    ctx.setLineDash([2, 4]);
    for (let i = stride; i < n; i += stride) {
      const x = xOf(i);
      ctx.beginPath();
      ctx.moveTo(x, plotT);
      ctx.lineTo(x, volB);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // volume
    const cw = Math.max(1, (plotW / n) * 0.62);
    candles.forEach((c, i) => {
      const x = xOf(i);
      const vh = ((c.v / maxV) * (volB - volT - 2));
      ctx.fillStyle = c.c >= c.o ? volUp : volDn;
      ctx.fillRect(x - cw / 2, volB - vh, cw, vh);
    });

    ctx.strokeStyle = border;
    ctx.beginPath();
    ctx.moveTo(plotL, volT);
    ctx.lineTo(plotR, volT);
    ctx.stroke();

    // candles
    candles.forEach((c, i) => {
      const x = xOf(i);
      const bull = c.c >= c.o;
      ctx.strokeStyle = bull ? green : red;
      ctx.fillStyle = bull ? green : red;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yOf(c.h));
      ctx.lineTo(x, yOf(c.l));
      ctx.stroke();
      const y1 = yOf(Math.max(c.o, c.c));
      const y2 = yOf(Math.min(c.o, c.c));
      const bh = Math.max(1, y2 - y1);
      ctx.fillRect(x - cw / 2, y1, cw, bh);
    });

    const dashLine = (price: number, color: string, label: string) => {
      const y = yOf(price);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      ctx.moveTo(plotL, y);
      ctx.lineTo(plotR, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = "600 10px IBM Plex Sans, sans-serif";
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, plotL + 6, y - 2);
    };

    if (focus && showLevels) {
      dashLine(focus.entry, blue, "Entry");
      dashLine(focus.sl, red, "SL");
      dashLine(focus.tp, green, "TP");
    }

    const fibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
    drawings.forEach((d) => {
      if (d.tool === "hline") {
        dashLine(d.p1, cssVar(wrap, "--tv-amber", "#ff9800"), formatPrice(d.p1, pair));
      } else if (d.tool === "trend" && d.i2 != null && d.p2 != null) {
        ctx.strokeStyle = blue;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(xOf(d.i1), yOf(d.p1));
        ctx.lineTo(xOf(d.i2), yOf(d.p2));
        ctx.stroke();
      } else if (d.tool === "fib" && d.p2 != null) {
        const p2 = d.p2;
        const loP = Math.min(d.p1, p2);
        const hiP = Math.max(d.p1, p2);
        const span = hiP - loP || 1;
        fibLevels.forEach((lv) => {
          const p = d.p1 > p2 ? d.p1 - span * lv : d.p1 + span * lv;
          ctx.strokeStyle = blue;
          ctx.globalAlpha = 0.55;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(plotL, yOf(p));
          ctx.lineTo(plotR, yOf(p));
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.globalAlpha = 1;
          ctx.fillStyle = blue;
          ctx.font = "10px IBM Plex Mono, monospace";
          ctx.fillText(`${(lv * 100).toFixed(1)}%`, plotL + 4, yOf(p) - 2);
        });
      }
    });

    const last = candles[n - 1];
    if (last) {
      const y = yOf(last.c);
      const up = last.c >= (candles[n - 2]?.c ?? last.o);
      ctx.strokeStyle = up ? green : red;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(plotL, y);
      ctx.lineTo(plotR, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = up ? green : red;
      ctx.fillRect(plotR, y - 9, scaleW, 18);
      ctx.fillStyle = "#fff";
      ctx.font = "600 11px IBM Plex Mono, monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(Number(last.c).toFixed(digits), plotR + scaleW / 2, y);
    }

    // time labels
    ctx.fillStyle = muted;
    ctx.font = "10px IBM Plex Mono, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const tStride = Math.max(1, Math.round(n / 7));
    for (let i = 0; i < n; i += tStride) {
      const label = candles[i].t.slice(5, 16).replace("T", " ");
      ctx.fillText(label, xOf(i), h - timeH + 6);
    }

    // scale border
    ctx.strokeStyle = border;
    ctx.beginPath();
    ctx.moveTo(plotR, 0);
    ctx.lineTo(plotR, h - timeH);
    ctx.moveTo(0, h - timeH);
    ctx.lineTo(w, h - timeH);
    ctx.stroke();

    // hover crosshair
    const hv = hover;
    if (hv && hv.i >= 0 && hv.i < n) {
      ctx.strokeStyle = cross;
      ctx.globalAlpha = 0.7;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(hv.x, plotT);
      ctx.lineTo(hv.x, volB);
      ctx.moveTo(plotL, hv.y);
      ctx.lineTo(plotR, hv.y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

  }, [candles, pair, focus, showLevels, drawings, hover]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [draw]);

  const eventPrice = (ev: React.MouseEvent) => {
    const rect = wrapRef.current!.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const L = layoutRef.current;
    const plotW = L.plotR - L.plotL;
    const plotH = L.plotB - L.plotT;
    const i = fromX(x, L.n || 1, L.plotL, plotW);
    const price = fromY(y, L.hi, L.lo, L.plotT, plotH);
    return { i, x, y, price };
  };

  const onMove = (ev: React.MouseEvent) => {
    if (!candles.length) return;
    const { i, x, y, price } = eventPrice(ev);
    const c = candles[i];
    if (!c) return;
    setHover({ i, x, y, price, c });
    onHover?.(c);
  };

  const snapPrice = (i: number, price: number) => {
    if (!magnet) return price;
    const c = candles[i];
    if (!c) return price;
    const pts = [c.o, c.h, c.l, c.c];
    return pts.reduce((best, p) => (Math.abs(p - price) < Math.abs(best - price) ? p : best), pts[0]);
  };

  const onClick = (ev: React.MouseEvent) => {
    if (tool === "cursor" || !candles.length) return;
    const { i, price: raw } = eventPrice(ev);
    const price = snapPrice(i, raw);
    if (tool === "hline") {
      onDraw({ id: `h-${Date.now()}`, tool: "hline", i1: i, p1: price });
      return;
    }
    if (!draft) {
      setDraft({ i, p: price });
      return;
    }
    if (tool === "trend" || tool === "fib") {
      onDraw({
        id: `${tool}-${Date.now()}`,
        tool,
        i1: draft.i,
        p1: draft.p,
        i2: i,
        p2: price,
      });
    }
    setDraft(null);
  };

  return (
    <div
      ref={wrapRef}
      className="relative h-full w-full overflow-hidden bg-tv-bg"
      onMouseMove={onMove}
      onMouseLeave={() => {
        setHover(null);
        onHover?.(null);
      }}
      onClick={onClick}
      style={{ cursor: tool === "cursor" ? "crosshair" : "cell" }}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />

      {hover && (
        <>
          <div
            className="absolute z-10 rounded-sm bg-tv-panel-2 px-1.5 py-0.5 font-mono text-micro text-tv-text"
            style={{
              left: Math.min(hover.x + 8, (wrapRef.current?.clientWidth ?? 200) - 90),
              top: (wrapRef.current?.clientHeight ?? 0) - 20,
            }}
          >
            {hover.c.t.slice(5, 16).replace("T", " ")}
          </div>
          <div
            className="absolute z-10 rounded-sm px-1.5 py-0.5 font-mono text-micro text-white"
            style={{
              right: 0,
              top: Math.max(4, hover.y - 8),
              background: "var(--tv-panel-2)",
              color: "var(--tv-text)",
              border: "1px solid var(--tv-border)",
            }}
          >
            {formatPrice(hover.price, pair)}
          </div>
        </>
      )}
    </div>
  );
}
