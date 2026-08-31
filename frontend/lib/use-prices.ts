"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PriceMap, PriceUpdate, PricePoint, ConnectionStatus } from "./types";

/** Max samples kept per ticker — enough for a sparkline and a few minutes of chart. */
const MAX_HISTORY = 600;

/** Delay before retrying a dropped SSE connection. */
const RETRY_DELAY_MS = 2000;

/**
 * Connects to the SSE price stream and accumulates per-ticker history since
 * page load. History lives in a ref (it is append-only and read imperatively by
 * the chart/sparklines) while the latest prices drive re-renders through state.
 */
export function usePrices() {
  const [prices, setPrices] = useState<PriceMap>({});
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const historyRef = useRef<Record<string, PricePoint[]>>({});
  /** Bumped on every batch so consumers reading the ref re-render. */
  const [revision, setRevision] = useState(0);

  const getHistory = useCallback((ticker: string): PricePoint[] => {
    return historyRef.current[ticker] ?? [];
  }, []);

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    /** Once we have connected at least once, later attempts are reconnections. */
    let hasConnected = false;

    function connect() {
      if (closed) return;
      setStatus(hasConnected ? "reconnecting" : "connecting");
      es = new EventSource("/api/stream/prices");

      es.onopen = () => {
        hasConnected = true;
        setStatus("connected");
      };

      es.onmessage = (event) => {
        let data: Record<string, PriceUpdate>;
        try {
          data = JSON.parse(event.data);
        } catch {
          return; // Ignore malformed events
        }
        if (!data || typeof data !== "object") return;

        setPrices(data);

        for (const [ticker, update] of Object.entries(data)) {
          if (typeof update?.price !== "number") continue;
          const arr = (historyRef.current[ticker] ??= []);
          // Timestamps come from the server in seconds; fall back to local time.
          const time = Math.floor(update.timestamp ?? Date.now() / 1000);
          const last = arr[arr.length - 1];
          if (last && last.time === time) {
            // Multiple ticks within the same second — keep the freshest.
            last.price = update.price;
          } else {
            arr.push({ time, price: update.price });
            if (arr.length > MAX_HISTORY) arr.shift();
          }
        }
        setRevision((r) => r + 1);
      };

      es.onerror = () => {
        es?.close();
        es = null;
        if (closed) return;
        setStatus("disconnected");
        // Reconnect ourselves so the status indicator reflects reality.
        retryTimeout = setTimeout(connect, RETRY_DELAY_MS);
      };
    }

    connect();

    return () => {
      closed = true;
      es?.close();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, []);

  return { prices, status, getHistory, revision };
}
