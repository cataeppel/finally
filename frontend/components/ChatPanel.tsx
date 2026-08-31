"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/types";
import { getChatHistory, sendChatMessage } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import Panel from "./Panel";

interface ChatPanelProps {
  onTradeExecuted: () => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}

const SUGGESTIONS = [
  "How is my portfolio doing?",
  "Buy 10 AAPL",
  "Add PYPL to my watchlist",
];

export default function ChatPanel({
  onTradeExecuted,
  collapsed = false,
  onToggleCollapsed,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Restore the persisted conversation so history survives a reload.
  useEffect(() => {
    let active = true;
    getChatHistory()
      .then((history) => {
        if (active && history.length) setMessages(history);
      })
      .catch(() => {
        // A fresh database has no history yet — start empty.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, loading]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: trimmed,
      created_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await sendChatMessage(trimmed);
      setMessages((prev) => [...prev, res.message]);
      const actions = res.message.actions;
      if (actions?.trades?.length || actions?.watchlist_changes?.length) {
        onTradeExecuted();
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: "assistant",
          content: err instanceof Error ? err.message : "Failed to get response",
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  const toggle = onToggleCollapsed ? (
    <button
      onClick={onToggleCollapsed}
      data-testid="chat-toggle"
      className="text-[10px] uppercase tracking-wider text-text-muted transition-colors hover:text-text-primary"
    >
      {collapsed ? "Expand" : "Collapse"}
    </button>
  ) : null;

  if (collapsed) {
    return (
      <Panel title="AI Assistant" actions={toggle} testId="chat-panel">
        <div className="p-3 text-[11px] text-text-muted">Chat collapsed.</div>
      </Panel>
    );
  }

  return (
    <Panel title="AI Assistant" actions={toggle} testId="chat-panel">
      <div className="flex h-full flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3">
          {messages.length === 0 && (
            <div className="space-y-3 pt-2">
              <p className="text-center text-[11px] leading-relaxed text-text-muted">
                I&apos;m FinAlly. Ask about your portfolio, request analysis, or tell me
                to trade.
              </p>
              <div className="flex flex-col gap-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded border border-border px-2 py-1 text-left text-[11px] text-text-secondary transition-colors hover:border-accent-blue hover:text-text-primary"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              data-testid={`chat-message-${msg.role}`}
              className={`text-xs ${msg.role === "user" ? "text-right" : "text-left"}`}
            >
              <div
                className={`inline-block max-w-[92%] rounded px-2.5 py-1.5 text-left ${
                  msg.role === "user"
                    ? "bg-accent-purple/30 text-text-primary"
                    : "border border-border bg-bg-primary text-text-primary"
                }`}
              >
                <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>

                {!!msg.actions?.trades?.length && (
                  <div className="mt-1.5 space-y-0.5 border-t border-border pt-1.5">
                    {msg.actions.trades.map((t, i) => (
                      <div key={i} className="text-[11px] text-accent-yellow tabular-nums">
                        ✓ {t.side.toUpperCase()} {t.quantity} {t.ticker} @{" "}
                        {formatPrice(t.price)}
                      </div>
                    ))}
                  </div>
                )}

                {!!msg.actions?.watchlist_changes?.length && (
                  <div className="mt-1.5 space-y-0.5 border-t border-border pt-1.5">
                    {msg.actions.watchlist_changes.map((w, i) => (
                      <div key={i} className="text-[11px] text-accent-blue">
                        ✓ Watchlist {w.action === "add" ? "+" : "−"} {w.ticker}
                      </div>
                    ))}
                  </div>
                )}

                {!!msg.actions?.errors?.length && (
                  <div className="mt-1.5 space-y-0.5 border-t border-border pt-1.5">
                    {msg.actions.errors.map((e, i) => (
                      <div key={i} className="text-[11px] text-loss">
                        ! {e}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="text-xs text-text-muted" data-testid="chat-loading">
              <span className="animate-pulse">Thinking...</span>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border p-2">
          <div className="flex gap-1.5">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") send(input);
              }}
              placeholder="Ask about your portfolio..."
              aria-label="Chat message"
              data-testid="chat-input"
              disabled={loading}
              className="min-w-0 flex-1 rounded border border-border bg-bg-primary px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:border-accent-blue focus:outline-none disabled:opacity-50"
            />
            <button
              onClick={() => send(input)}
              disabled={loading || !input.trim()}
              data-testid="chat-send"
              className="rounded bg-accent-purple px-3 py-1.5 text-xs font-bold text-white transition-colors hover:bg-accent-purple/80 disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}
