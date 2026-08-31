"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  AreaSeries,
  type IChartApi,
  type ISeriesApi,
  type AreaData,
  type Time,
} from "lightweight-charts";
import type { PriceMap, PricePoint } from "@/lib/types";
import { formatPrice, formatPercent } from "@/lib/format";
import Panel, { PanelEmpty } from "./Panel";

interface PriceChartProps {
  ticker: string | null;
  getHistory: (ticker: string) => PricePoint[];
  prices: PriceMap;
  /** Increments on every SSE batch; drives the incremental chart update. */
  revision: number;
}

export default function PriceChart({ ticker, getHistory, prices, revision }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  /** Ticker currently loaded into the series, so we know when to reseed. */
  const loadedTickerRef = useRef<string | null>(null);

  // Create the chart once and keep it for the component's lifetime.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: "#6e7681",
        fontFamily: "ui-monospace, monospace",
        fontSize: 10,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#1c2128" },
        horzLines: { color: "#1c2128" },
      },
      crosshair: {
        vertLine: { color: "#484f58", labelBackgroundColor: "#209dd7", width: 1 },
        horzLine: { color: "#484f58", labelBackgroundColor: "#209dd7" },
      },
      rightPriceScale: { borderColor: "#30363d" },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: true,
      },
      handleScale: { axisPressedMouseMove: false },
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: "#209dd7",
      topColor: "rgba(32, 157, 215, 0.28)",
      bottomColor: "rgba(32, 157, 215, 0)",
      lineWidth: 2,
      priceLineVisible: true,
      priceLineColor: "#ecad0a",
      crosshairMarkerRadius: 3,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) chart.applyOptions({ width, height });
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      loadedTickerRef.current = null;
    };
  }, []);

  // Reseed on ticker change, then append the newest sample on each SSE batch.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !ticker) return;

    const history = getHistory(ticker);

    if (loadedTickerRef.current !== ticker) {
      loadedTickerRef.current = ticker;
      const data: AreaData[] = history.map((p) => ({
        time: p.time as Time,
        value: p.price,
      }));
      series.setData(data);
    } else {
      const latest = history[history.length - 1];
      if (!latest) return;
      // `update` also replaces the last point when the timestamp repeats.
      series.update({ time: latest.time as Time, value: latest.price });
    }

    // The window grows from a single tick at page load, so refit on every
    // batch — otherwise the time scale keeps the tiny initial span and the
    // chart shows only the newest couple of points.
    if (history.length > 1) chartRef.current?.timeScale().fitContent();
  }, [ticker, getHistory, revision]);

  const quote = ticker ? prices[ticker] : undefined;

  const readout = quote ? (
    <div className="flex items-baseline gap-2">
      <span className="text-sm font-bold tabular-nums text-text-primary">
        {formatPrice(quote.price)}
      </span>
      <span
        className={`text-[11px] font-bold tabular-nums ${
          quote.direction === "up"
            ? "text-gain"
            : quote.direction === "down"
              ? "text-loss"
              : "text-text-muted"
        }`}
      >
        {formatPercent(quote.change_percent)}
      </span>
    </div>
  ) : null;

  return (
    <Panel
      title={ticker ? `${ticker} · Price` : "Price Chart"}
      actions={readout}
      testId="price-chart"
    >
      <div className="relative h-full w-full">
        <div ref={containerRef} className="h-full w-full" />
        {!ticker && (
          <div className="absolute inset-0">
            <PanelEmpty>Select a ticker from the watchlist</PanelEmpty>
          </div>
        )}
      </div>
    </Panel>
  );
}
