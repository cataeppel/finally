import { act, renderHook } from "@testing-library/react";
import { usePrices } from "@/lib/use-prices";

/** Minimal EventSource stand-in — jsdom does not implement one. */
class MockEventSource {
  static instances: MockEventSource[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  static get latest() {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }

  static reset() {
    MockEventSource.instances = [];
  }
}

function tick(ticker: string, price: number, previous: number, timestamp: number) {
  return {
    [ticker]: {
      ticker,
      price,
      previous_price: previous,
      timestamp,
      change: price - previous,
      change_percent: ((price - previous) / previous) * 100,
      direction: price > previous ? "up" : "down",
    },
  };
}

function emit(payload: unknown) {
  act(() => {
    MockEventSource.latest.onmessage?.({ data: JSON.stringify(payload) });
  });
}

beforeEach(() => {
  MockEventSource.reset();
  jest.useFakeTimers();
  (global as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

afterEach(() => {
  jest.useRealTimers();
});

describe("usePrices", () => {
  it("subscribes to the SSE price stream", () => {
    renderHook(() => usePrices());
    expect(MockEventSource.latest.url).toBe("/api/stream/prices");
  });

  it("starts as connecting and reports connected once the stream opens", () => {
    const { result } = renderHook(() => usePrices());
    expect(result.current.status).toBe("connecting");

    act(() => MockEventSource.latest.onopen?.());
    expect(result.current.status).toBe("connected");
  });

  it("exposes the latest prices from each event", () => {
    const { result } = renderHook(() => usePrices());
    act(() => MockEventSource.latest.onopen?.());

    emit(tick("AAPL", 190.5, 190, 1000));
    expect(result.current.prices.AAPL.price).toBe(190.5);

    emit(tick("AAPL", 191, 190.5, 1001));
    expect(result.current.prices.AAPL.price).toBe(191);
  });

  it("accumulates timestamped history for sparklines", () => {
    const { result } = renderHook(() => usePrices());
    emit(tick("AAPL", 190, 189, 1000));
    emit(tick("AAPL", 191, 190, 1001));
    emit(tick("AAPL", 192, 191, 1002));

    expect(result.current.getHistory("AAPL")).toEqual([
      { time: 1000, price: 190 },
      { time: 1001, price: 191 },
      { time: 1002, price: 192 },
    ]);
  });

  it("collapses multiple ticks within the same second", () => {
    const { result } = renderHook(() => usePrices());
    emit(tick("AAPL", 190, 189, 1000));
    emit(tick("AAPL", 190.7, 190, 1000.4));

    expect(result.current.getHistory("AAPL")).toEqual([{ time: 1000, price: 190.7 }]);
  });

  it("returns an empty history for an unknown ticker", () => {
    const { result } = renderHook(() => usePrices());
    expect(result.current.getHistory("NOPE")).toEqual([]);
  });

  it("bumps the revision counter on each batch", () => {
    const { result } = renderHook(() => usePrices());
    const before = result.current.revision;
    emit(tick("AAPL", 190, 189, 1000));
    expect(result.current.revision).toBeGreaterThan(before);
  });

  it("ignores malformed event payloads", () => {
    const { result } = renderHook(() => usePrices());
    act(() => {
      MockEventSource.latest.onmessage?.({ data: "not json" });
    });
    expect(result.current.prices).toEqual({});
  });

  it("reports disconnected then reconnects after the retry delay", () => {
    const { result } = renderHook(() => usePrices());
    act(() => MockEventSource.latest.onopen?.());

    act(() => MockEventSource.latest.onerror?.());
    expect(result.current.status).toBe("disconnected");
    expect(MockEventSource.instances).toHaveLength(1);

    act(() => {
      jest.advanceTimersByTime(2000);
    });

    // A second connection is opened, and having connected before it is a retry.
    expect(MockEventSource.instances).toHaveLength(2);
    expect(result.current.status).toBe("reconnecting");

    act(() => MockEventSource.latest.onopen?.());
    expect(result.current.status).toBe("connected");
  });

  it("closes the stream and cancels the retry on unmount", () => {
    const { unmount } = renderHook(() => usePrices());
    const es = MockEventSource.latest;

    unmount();

    expect(es.closed).toBe(true);
    act(() => {
      jest.advanceTimersByTime(5000);
    });
    expect(MockEventSource.instances).toHaveLength(1);
  });
});
