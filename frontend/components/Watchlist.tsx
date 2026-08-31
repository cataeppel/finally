"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PriceMap, PricePoint, WatchlistItem } from "@/lib/types";
import { formatPrice, formatPercent } from "@/lib/format";
import { addToWatchlist, removeFromWatchlist } from "@/lib/api";
import Panel, { PanelEmpty } from "./Panel";
import Sparkline from "./Sparkline";

interface WatchlistProps {
  items: WatchlistItem[];
  prices: PriceMap;
  getHistory: (ticker: string) => PricePoint[];
  selectedTicker: string | null;
  onSelectTicker: (ticker: string) => void;
  onRefresh: () => void;
}

/** Samples shown in each row's sparkline. */
const SPARK_POINTS = 60;

export default function Watchlist({
  items,
  prices,
  getHistory,
  selectedTicker,
  onSelectTicker,
  onRefresh,
}: WatchlistProps) {
  const [addInput, setAddInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const prevPricesRef = useRef<Record<string, number>>({});
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  // Flash each row green/red on every tick. Applied imperatively so the
  // animation restarts cleanly without re-rendering the row.
  useEffect(() => {
    for (const [ticker, update] of Object.entries(prices)) {
      const prev = prevPricesRef.current[ticker];
      if (prev !== undefined && prev !== update.price) {
        const row = rowRefs.current[ticker];
        if (row) {
          const cls = update.price > prev ? "price-flash-up" : "price-flash-down";
          row.classList.remove("price-flash-up", "price-flash-down");
          void row.offsetWidth; // Force reflow so the animation replays
          row.classList.add(cls);
        }
      }
      prevPricesRef.current[ticker] = update.price;
    }
  }, [prices]);

  const handleAdd = useCallback(async () => {
    const ticker = addInput.trim().toUpperCase();
    if (!ticker) return;
    try {
      setError(null);
      await addToWatchlist(ticker);
      setAddInput("");
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not add ${ticker}`);
    }
  }, [addInput, onRefresh]);

  const handleRemove = useCallback(
    async (ticker: string, e: React.MouseEvent) => {
      e.stopPropagation();
      try {
        setError(null);
        await removeFromWatchlist(ticker);
        onRefresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : `Could not remove ${ticker}`);
      }
    },
    [onRefresh]
  );

  const addControls = (
    <div className="flex items-center gap-1">
      <input
        type="text"
        value={addInput}
        onChange={(e) => setAddInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") handleAdd();
        }}
        placeholder="Add symbol"
        aria-label="Add ticker to watchlist"
        data-testid="watchlist-add-input"
        className="w-24 rounded border border-border bg-bg-primary px-1.5 py-0.5 text-[11px] uppercase text-text-primary placeholder:normal-case placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
      />
      <button
        onClick={handleAdd}
        aria-label="Add to watchlist"
        data-testid="watchlist-add-button"
        className="rounded border border-border px-1.5 text-[11px] leading-5 text-accent-blue transition-colors hover:border-accent-blue hover:text-accent-yellow"
      >
        +
      </button>
    </div>
  );

  return (
    <Panel title="Watchlist" actions={addControls}>
      <div className="flex h-full flex-col">
        {error && (
          <div
            className="border-b border-border bg-loss/10 px-3 py-1 text-[11px] text-loss"
            data-testid="watchlist-error"
          >
            {error}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {items.length === 0 ? (
            <PanelEmpty>Watchlist is empty — add a symbol above.</PanelEmpty>
          ) : (
            <table className="w-full text-xs" data-testid="watchlist">
              <thead className="sticky top-0 z-10 bg-bg-panel">
                <tr className="border-b border-border text-[10px] uppercase tracking-wider text-text-muted">
                  <th className="px-3 py-1.5 text-left font-normal">Ticker</th>
                  <th className="px-2 py-1.5 text-right font-normal">Price</th>
                  <th className="px-2 py-1.5 text-right font-normal">Chg%</th>
                  <th className="px-2 py-1.5 text-right font-normal">Chart</th>
                  <th className="w-6" />
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const update = prices[item.ticker];
                  const price = update?.price ?? item.price ?? 0;
                  const isSelected = selectedTicker === item.ticker;
                  const history = getHistory(item.ticker);
                  const spark = history.slice(-SPARK_POINTS).map((p) => p.price);

                  // Change since the session opened, which is stable and matches
                  // the sparkline. Until two samples exist, fall back to the
                  // tick-over-tick delta the stream reports.
                  const sessionOpen = history[0]?.price;
                  const changePct =
                    sessionOpen && history.length > 1
                      ? ((price - sessionOpen) / sessionOpen) * 100
                      : (update?.change_percent ?? item.change_percent ?? 0);
                  const trend =
                    changePct > 0 ? "up" : changePct < 0 ? "down" : "flat";

                  return (
                    <tr
                      key={item.ticker}
                      ref={(el) => {
                        rowRefs.current[item.ticker] = el;
                      }}
                      onClick={() => onSelectTicker(item.ticker)}
                      data-testid={`watchlist-row-${item.ticker}`}
                      className={`group cursor-pointer border-b border-border/40 transition-colors hover:bg-bg-hover ${
                        isSelected ? "bg-bg-hover" : ""
                      }`}
                    >
                      <td className="relative px-3 py-1.5 font-bold text-text-primary">
                        {isSelected && (
                          <span className="absolute inset-y-0 left-0 w-0.5 bg-accent-yellow" />
                        )}
                        {item.ticker}
                      </td>
                      <td
                        data-testid={`watchlist-price-${item.ticker}`}
                        className="flash-target px-2 py-1.5 text-right tabular-nums text-text-primary"
                      >
                        {formatPrice(price)}
                      </td>
                      <td
                        className={`px-2 py-1.5 text-right tabular-nums ${
                          trend === "up"
                            ? "text-gain"
                            : trend === "down"
                              ? "text-loss"
                              : "text-text-muted"
                        }`}
                      >
                        {formatPercent(changePct)}
                      </td>
                      <td className="px-2 py-1 text-right">
                        <Sparkline
                          data={spark}
                          testId={`watchlist-sparkline-${item.ticker}`}
                        />
                      </td>
                      <td className="pr-2">
                        <button
                          onClick={(e) => handleRemove(item.ticker, e)}
                          data-testid={`watchlist-remove-${item.ticker}`}
                          className="text-xs leading-none text-text-muted opacity-40 transition-opacity hover:text-loss group-hover:opacity-100 focus:opacity-100"
                          title={`Remove ${item.ticker} from watchlist`}
                          aria-label={`Remove ${item.ticker} from watchlist`}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Panel>
  );
}
