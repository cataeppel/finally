"use client";

import type { ConnectionStatus } from "@/lib/types";
import { formatCurrency, formatPercent } from "@/lib/format";

interface HeaderProps {
  totalValue: number;
  cashBalance: number;
  status: ConnectionStatus;
  /** Total unrealized P&L across open positions. */
  unrealizedPnl?: number;
}

const statusColors: Record<ConnectionStatus, string> = {
  connected: "bg-gain",
  connecting: "bg-accent-yellow",
  reconnecting: "bg-accent-yellow",
  disconnected: "bg-loss",
};

const statusLabels: Record<ConnectionStatus, string> = {
  connected: "Live",
  connecting: "Connecting...",
  reconnecting: "Reconnecting...",
  disconnected: "Disconnected",
};

/** The starting cash the account is seeded with, used as the P&L baseline. */
const STARTING_EQUITY = 10000;

function Stat({
  label,
  value,
  testId,
  tone = "neutral",
}: {
  label: string;
  value: string;
  testId?: string;
  tone?: "neutral" | "gain" | "loss";
}) {
  const toneClass =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-text-primary";
  return (
    <div className="flex flex-col items-end leading-tight">
      <span className="text-[9px] uppercase tracking-[0.14em] text-text-muted">{label}</span>
      <span className={`text-sm font-bold tabular-nums ${toneClass}`} data-testid={testId}>
        {value}
      </span>
    </div>
  );
}

export default function Header({
  totalValue,
  cashBalance,
  status,
  unrealizedPnl = 0,
}: HeaderProps) {
  const totalReturn = totalValue - STARTING_EQUITY;
  const totalReturnPct = (totalReturn / STARTING_EQUITY) * 100;
  const returnTone = totalReturn >= 0 ? "gain" : "loss";

  return (
    <header className="flex shrink-0 items-center justify-between border-b border-border bg-bg-panel px-4 py-2">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg font-bold tracking-wide text-accent-yellow">FinAlly</h1>
        <span className="hidden text-[10px] uppercase tracking-[0.16em] text-text-muted sm:inline">
          AI Trading Workstation
        </span>
      </div>

      <div className="flex items-center gap-5">
        <Stat label="Portfolio" value={formatCurrency(totalValue)} testId="header-portfolio-value" />
        <Stat label="Cash" value={formatCurrency(cashBalance)} testId="header-cash" />
        <Stat
          label="Open P&L"
          value={formatCurrency(unrealizedPnl)}
          testId="header-unrealized-pnl"
          tone={unrealizedPnl >= 0 ? "gain" : "loss"}
        />
        <Stat
          label="Total Return"
          value={formatPercent(totalReturnPct)}
          testId="header-total-return"
          tone={returnTone}
        />

        <div className="flex items-center gap-1.5 border-l border-border pl-4">
          <span
            className={`inline-block h-2 w-2 rounded-full ${statusColors[status]} ${
              status === "connected" ? "animate-pulse" : ""
            }`}
            data-testid="connection-dot"
            aria-hidden="true"
          />
          <span
            className="text-[11px] text-text-secondary"
            data-testid="connection-status"
            role="status"
          >
            {statusLabels[status]}
          </span>
        </div>
      </div>
    </header>
  );
}
