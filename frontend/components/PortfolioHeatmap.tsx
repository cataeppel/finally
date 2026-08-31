"use client";

import { useEffect, useRef, useState } from "react";
import type { Position } from "@/lib/types";
import { formatCurrency, formatPercent } from "@/lib/format";
import { squarify } from "@/lib/treemap";
import Panel from "./Panel";

interface PortfolioHeatmapProps {
  positions: Position[];
  onSelectTicker?: (ticker: string) => void;
}

/** Diverging green/red scale keyed on a position's P&L percentage. */
export function pnlColor(pct: number): string {
  if (pct > 5) return "#1a7f37";
  if (pct > 2) return "#238636";
  if (pct > 0) return "#1c4529";
  if (pct === 0) return "#30363d";
  if (pct > -2) return "#5c2523";
  if (pct > -5) return "#a72f2b";
  return "#da3633";
}

/** Fallback box used before the ResizeObserver reports real dimensions. */
const FALLBACK_SIZE = { width: 400, height: 260 };

export default function PortfolioHeatmap({
  positions,
  onSelectTicker,
}: PortfolioHeatmapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(FALLBACK_SIZE);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) setSize({ width, height });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [positions.length]);

  const rects = squarify(
    positions.map((p) => Math.max(p.market_value, 0)),
    size.width,
    size.height
  );

  return (
    <Panel title="Portfolio Heatmap">
      {positions.length === 0 ? (
        <div className="flex h-full items-center justify-center text-xs text-text-muted">
          <span data-testid="heatmap-empty">No positions to display</span>
        </div>
      ) : (
        <div ref={containerRef} className="relative h-full w-full p-1" data-testid="heatmap">
          {positions.map((pos, i) => {
            const rect = rects[i];
            if (rect.w <= 0 || rect.h <= 0) return null;
            // Tight tiles shrink the type rather than dropping it: the P&L
            // percentage is part of the heatmap's contract, not decoration.
            const compact = rect.w < 64 || rect.h < 40;

            return (
              <button
                key={pos.ticker}
                type="button"
                onClick={() => onSelectTicker?.(pos.ticker)}
                data-testid={`heatmap-tile-${pos.ticker}`}
                title={`${pos.ticker} — ${formatCurrency(pos.market_value)} (${formatPercent(
                  pos.pnl_percent
                )})`}
                className="absolute flex flex-col items-center justify-center overflow-hidden rounded-[2px] border border-bg-primary/60 text-center transition-[filter] hover:brightness-125"
                style={{
                  left: `${(rect.x / size.width) * 100}%`,
                  top: `${(rect.y / size.height) * 100}%`,
                  width: `${(rect.w / size.width) * 100}%`,
                  height: `${(rect.h / size.height) * 100}%`,
                  backgroundColor: pnlColor(pos.pnl_percent),
                }}
              >
                <span
                  className={`px-1 font-bold leading-tight text-white ${
                    compact ? "text-[9px]" : "text-[11px]"
                  }`}
                >
                  {pos.ticker}
                </span>
                <span
                  className={`px-1 leading-tight text-white/85 tabular-nums ${
                    compact ? "text-[8px]" : "text-[10px]"
                  }`}
                >
                  {formatPercent(pos.pnl_percent)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
