"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import type { PortfolioSnapshot } from "@/lib/types";
import { getPortfolioHistory } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";
import Panel from "./Panel";

/** Seeded starting equity — the break-even line for the P&L chart. */
const STARTING_EQUITY = 10000;

/** Snapshots are written every 30s server-side; poll a little faster. */
const POLL_MS = 15000;

interface PnlChartProps {
  /** Bumped by the parent after a trade so the new snapshot is picked up. */
  refreshKey?: number;
}

export default function PnlChart({ refreshKey = 0 }: PnlChartProps) {
  const [data, setData] = useState<PortfolioSnapshot[]>([]);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const snapshots = await getPortfolioHistory();
        if (active) setData(snapshots);
      } catch {
        // Transient — the next poll will retry
      }
    }

    load();
    const interval = setInterval(load, POLL_MS);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [refreshKey]);

  const chartData = useMemo(
    () =>
      data.map((s) => ({
        time: new Date(s.recorded_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
        value: s.total_value,
      })),
    [data]
  );

  const lastValue = chartData.length ? chartData[chartData.length - 1].value : STARTING_EQUITY;
  const change = lastValue - STARTING_EQUITY;
  const isPositive = change >= 0;
  const lineColor = isPositive ? "#26a641" : "#f85149";

  const summary = (
    <span
      className={`text-[11px] font-bold tabular-nums ${isPositive ? "text-gain" : "text-loss"}`}
      data-testid="pnl-summary"
    >
      {formatCurrency(change)} ({formatPercent((change / STARTING_EQUITY) * 100)})
    </span>
  );

  return (
    <Panel title="Portfolio P&L" actions={summary}>
      {chartData.length < 2 ? (
        <div className="flex h-full items-center justify-center text-xs text-text-muted">
          <span data-testid="pnl-chart-empty">Waiting for portfolio history...</span>
        </div>
      ) : (
        <div className="h-full w-full p-2" data-testid="pnl-chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={lineColor} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={lineColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="2 4" stroke="#21262d" vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fill: "#6e7681", fontSize: 9 }}
                stroke="#30363d"
                tickLine={false}
                minTickGap={32}
              />
              <YAxis
                tick={{ fill: "#6e7681", fontSize: 9 }}
                stroke="#30363d"
                tickLine={false}
                width={46}
                tickFormatter={(v) => `$${(v / 1000).toFixed(2)}k`}
                domain={["dataMin - 50", "dataMax + 50"]}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#161b22",
                  border: "1px solid #30363d",
                  borderRadius: "4px",
                  fontSize: "11px",
                  color: "#e6edf3",
                }}
                labelStyle={{ color: "#8b949e" }}
                formatter={(value) => [formatCurrency(value as number), "Value"]}
              />
              <ReferenceLine
                y={STARTING_EQUITY}
                stroke="#6e7681"
                strokeDasharray="3 3"
                strokeWidth={1}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={lineColor}
                fill="url(#pnlFill)"
                strokeWidth={1.75}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  );
}
