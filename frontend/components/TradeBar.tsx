"use client";

import { useEffect, useState } from "react";
import { executeTrade } from "@/lib/api";
import type { PriceMap } from "@/lib/types";
import { formatCurrency, formatPrice } from "@/lib/format";

interface TradeBarProps {
  prices: PriceMap;
  onTradeExecuted: () => void;
  /** Ticker selected in the watchlist; prefills the form. */
  selectedTicker?: string | null;
  /** Available cash, used for the estimated-cost readout. */
  cashBalance?: number;
}

export default function TradeBar({
  prices,
  onTradeExecuted,
  selectedTicker,
  cashBalance,
}: TradeBarProps) {
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [isError, setIsError] = useState(false);
  const [pending, setPending] = useState(false);
  /** Set once the user edits the ticker, so selection stops overriding them. */
  const [touched, setTouched] = useState(false);

  // Follow the watchlist selection until the user types their own symbol.
  useEffect(() => {
    if (!touched && selectedTicker) setTicker(selectedTicker);
  }, [selectedTicker, touched]);

  const symbol = ticker.trim().toUpperCase();
  const currentPrice = prices[symbol]?.price;
  const qtyValue = parseFloat(quantity);
  const estimate =
    currentPrice && qtyValue > 0 ? currentPrice * qtyValue : null;

  async function handleTrade(side: "buy" | "sell") {
    if (pending) return;
    if (!symbol || !Number.isFinite(qtyValue) || qtyValue <= 0) {
      setStatus("Enter a valid ticker and quantity");
      setIsError(true);
      return;
    }

    setPending(true);
    try {
      setStatus(null);
      const result = await executeTrade({ ticker: symbol, quantity: qtyValue, side });
      setStatus(
        `${side.toUpperCase()} ${result.quantity} ${result.ticker} @ ${formatPrice(result.price)}`
      );
      setIsError(false);
      setQuantity("");
      onTradeExecuted();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Trade failed");
      setIsError(true);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="shrink-0 border-t border-border bg-bg-panel px-3 py-2">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-text-secondary">
          Trade
        </span>
        <span className="text-[10px] text-text-muted">Market order · instant fill</span>
      </div>

      <div className="flex items-center gap-1.5">
        <input
          type="text"
          value={ticker}
          onChange={(e) => {
            setTicker(e.target.value);
            setTouched(true);
          }}
          placeholder="Ticker"
          aria-label="Ticker"
          data-testid="trade-ticker"
          className="w-20 rounded border border-border bg-bg-primary px-2 py-1 text-xs uppercase tabular-nums text-text-primary placeholder:normal-case placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleTrade("buy");
          }}
          placeholder="Qty"
          aria-label="Quantity"
          data-testid="trade-qty"
          min="0"
          step="any"
          className="w-16 rounded border border-border bg-bg-primary px-2 py-1 text-xs tabular-nums text-text-primary placeholder:text-text-muted focus:border-accent-blue focus:outline-none"
        />
        <button
          onClick={() => handleTrade("buy")}
          disabled={pending}
          data-testid="trade-buy"
          className="flex-1 rounded bg-gain px-3 py-1 text-xs font-bold text-white transition-colors hover:bg-gain/80 disabled:opacity-50"
        >
          BUY
        </button>
        <button
          onClick={() => handleTrade("sell")}
          disabled={pending}
          data-testid="trade-sell"
          className="flex-1 rounded bg-loss px-3 py-1 text-xs font-bold text-white transition-colors hover:bg-loss/80 disabled:opacity-50"
        >
          SELL
        </button>
      </div>

      <div className="mt-1.5 flex items-center justify-between text-[10px] tabular-nums text-text-muted">
        <span data-testid="trade-estimate">
          {currentPrice ? `@ ${formatPrice(currentPrice)}` : symbol ? "No live price" : " "}
          {estimate !== null && ` · est. ${formatCurrency(estimate)}`}
        </span>
        {cashBalance !== undefined && <span>Cash {formatCurrency(cashBalance)}</span>}
      </div>

      {status && (
        <div
          data-testid="trade-status"
          className={`mt-1 text-[11px] ${isError ? "text-loss" : "text-gain"}`}
        >
          {status}
        </div>
      )}
    </div>
  );
}
