"use client";

import type { Position } from "@/lib/types";
import { formatCurrency, formatPercent, formatPrice } from "@/lib/format";
import Panel from "./Panel";

interface PositionsTableProps {
  positions: Position[];
  onSelectTicker?: (ticker: string) => void;
}

export default function PositionsTable({ positions, onSelectTicker }: PositionsTableProps) {
  const totalPnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0);

  const summary =
    positions.length > 0 ? (
      <span
        className={`text-[11px] font-bold tabular-nums ${
          totalPnl >= 0 ? "text-gain" : "text-loss"
        }`}
        data-testid="positions-total-pnl"
      >
        {formatCurrency(totalPnl)}
      </span>
    ) : null;

  return (
    <Panel title="Positions" actions={summary}>
      {positions.length === 0 ? (
        <div className="flex h-full items-center justify-center text-xs text-text-muted">
          <span data-testid="positions-empty">No open positions</span>
        </div>
      ) : (
        <div className="h-full overflow-auto">
          <table className="w-full text-xs" data-testid="positions-table">
            <thead className="sticky top-0 z-10 bg-bg-panel">
              <tr className="border-b border-border text-[10px] uppercase tracking-wider text-text-muted">
                <th className="px-3 py-1.5 text-left font-normal">Ticker</th>
                <th className="px-2 py-1.5 text-right font-normal">Qty</th>
                <th className="px-2 py-1.5 text-right font-normal">Avg Cost</th>
                <th className="px-2 py-1.5 text-right font-normal">Price</th>
                <th className="px-2 py-1.5 text-right font-normal">Value</th>
                <th className="px-2 py-1.5 text-right font-normal">P&L</th>
                <th className="px-3 py-1.5 text-right font-normal">P&L%</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => (
                <tr
                  key={pos.ticker}
                  data-testid={`position-row-${pos.ticker}`}
                  onClick={() => onSelectTicker?.(pos.ticker)}
                  className={`border-b border-border/40 transition-colors hover:bg-bg-hover ${
                    onSelectTicker ? "cursor-pointer" : ""
                  }`}
                >
                  <td className="px-3 py-1.5 font-bold text-text-primary">{pos.ticker}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{pos.quantity}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-text-secondary">
                    {formatPrice(pos.avg_cost)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">
                    {formatPrice(pos.current_price)}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums text-text-secondary">
                    {formatPrice(pos.market_value)}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right tabular-nums ${
                      pos.unrealized_pnl >= 0 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {formatCurrency(pos.unrealized_pnl)}
                  </td>
                  <td
                    className={`px-3 py-1.5 text-right font-bold tabular-nums ${
                      pos.pnl_percent >= 0 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {formatPercent(pos.pnl_percent)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
