"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePrices } from "@/lib/use-prices";
import { getPortfolio, getWatchlist } from "@/lib/api";
import type { Portfolio, WatchlistItem } from "@/lib/types";
import Header from "@/components/Header";
import Watchlist from "@/components/Watchlist";
import PriceChart from "@/components/PriceChart";
import PositionsTable from "@/components/PositionsTable";
import PortfolioHeatmap from "@/components/PortfolioHeatmap";
import PnlChart from "@/components/PnlChart";
import TradeBar from "@/components/TradeBar";
import ChatPanel from "@/components/ChatPanel";

/** How often portfolio/watchlist REST state is re-polled. */
const REFRESH_MS = 5000;

export default function Home() {
  const { prices, status, getHistory, revision } = usePrices();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [userSelectedTicker, setUserSelectedTicker] = useState<string | null>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  /** Bumped after a trade so the P&L chart refetches immediately. */
  const [tradeSeq, setTradeSeq] = useState(0);

  // Fall back to the first watchlist entry until the user picks a ticker.
  const selectedTicker = useMemo(() => {
    if (userSelectedTicker) return userSelectedTicker;
    return watchlist.length > 0 ? watchlist[0].ticker : null;
  }, [userSelectedTicker, watchlist]);

  const refreshPortfolio = useCallback(async () => {
    try {
      setPortfolio(await getPortfolio());
    } catch {
      // Transient — the next poll retries
    }
  }, []);

  const refreshWatchlist = useCallback(async () => {
    try {
      setWatchlist(await getWatchlist());
    } catch {
      // Transient — the next poll retries
    }
  }, []);

  const refreshAll = useCallback(() => {
    refreshPortfolio();
    refreshWatchlist();
    setTradeSeq((n) => n + 1);
  }, [refreshPortfolio, refreshWatchlist]);

  // Poll REST state on a timer. The first tick fires immediately on a zero
  // delay so the initial fetch stays outside the effect body itself.
  useEffect(() => {
    const poll = () => {
      refreshPortfolio();
      refreshWatchlist();
    };
    const first = setTimeout(poll, 0);
    const interval = setInterval(poll, REFRESH_MS);
    return () => {
      clearTimeout(first);
      clearInterval(interval);
    };
  }, [refreshPortfolio, refreshWatchlist]);

  // Mark positions to live prices between REST polls so the header, positions
  // table and heatmap move with the stream rather than lurching every 5s.
  const livePortfolio = useMemo<Portfolio | null>(() => {
    if (!portfolio) return null;

    let marketValue = 0;
    let unrealized = 0;
    const positions = portfolio.positions.map((pos) => {
      const price = prices[pos.ticker]?.price ?? pos.current_price;
      const value = price * pos.quantity;
      const costBasis = pos.avg_cost * pos.quantity;
      const pnl = value - costBasis;
      marketValue += value;
      unrealized += pnl;
      return {
        ...pos,
        current_price: price,
        market_value: value,
        unrealized_pnl: pnl,
        pnl_percent: costBasis ? (pnl / costBasis) * 100 : 0,
      };
    });

    return {
      positions,
      cash_balance: portfolio.cash_balance,
      total_value: portfolio.cash_balance + marketValue,
      unrealized_pnl: unrealized,
    };
  }, [portfolio, prices]);

  const positions = livePortfolio?.positions ?? [];

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg-primary">
      <Header
        totalValue={livePortfolio?.total_value ?? 10000}
        cashBalance={livePortfolio?.cash_balance ?? 10000}
        unrealizedPnl={livePortfolio?.unrealized_pnl ?? 0}
        status={status}
      />

      <main className="flex min-h-0 flex-1">
        {/* Left rail: watchlist over the trade ticket */}
        <div className="flex w-72 shrink-0 flex-col border-r border-border bg-bg-panel xl:w-80">
          <div className="min-h-0 flex-1">
            <Watchlist
              items={watchlist}
              prices={prices}
              getHistory={getHistory}
              selectedTicker={selectedTicker}
              onSelectTicker={setUserSelectedTicker}
              onRefresh={refreshWatchlist}
            />
          </div>
          <TradeBar
            prices={prices}
            selectedTicker={selectedTicker}
            cashBalance={livePortfolio?.cash_balance}
            onTradeExecuted={refreshAll}
          />
        </div>

        {/* Centre: price chart and heatmap over positions and P&L */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-[3]">
            <div className="min-w-0 flex-[2] border-r border-border">
              <PriceChart
                ticker={selectedTicker}
                getHistory={getHistory}
                prices={prices}
                revision={revision}
              />
            </div>
            <div className="min-w-0 flex-1">
              <PortfolioHeatmap
                positions={positions}
                onSelectTicker={setUserSelectedTicker}
              />
            </div>
          </div>

          <div className="flex min-h-0 flex-[2] border-t border-border">
            <div className="min-w-0 flex-1 border-r border-border">
              <PositionsTable positions={positions} onSelectTicker={setUserSelectedTicker} />
            </div>
            <div className="min-w-0 flex-1">
              <PnlChart refreshKey={tradeSeq} />
            </div>
          </div>
        </div>

        {/* Right rail: AI chat */}
        <div
          className={`shrink-0 border-l border-border bg-bg-panel transition-[width] ${
            chatCollapsed ? "w-40" : "w-80 xl:w-96"
          }`}
        >
          <ChatPanel
            onTradeExecuted={refreshAll}
            collapsed={chatCollapsed}
            onToggleCollapsed={() => setChatCollapsed((c) => !c)}
          />
        </div>
      </main>
    </div>
  );
}
