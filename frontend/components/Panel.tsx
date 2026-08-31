"use client";

import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  /** Optional right-aligned content in the title bar (inputs, readouts). */
  actions?: ReactNode;
  children: ReactNode;
  testId?: string;
}

/**
 * Standard panel chrome: a compact uppercase title bar over a scrollable body.
 * Every panel keeps its header even when empty, so the layout stays stable and
 * section labels remain findable.
 */
export default function Panel({ title, actions, children, testId }: PanelProps) {
  return (
    <section className="flex h-full min-h-0 flex-col" data-testid={testId}>
      <div className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-border bg-bg-panel px-3">
        <h2 className="text-[10px] font-bold uppercase tracking-[0.12em] text-text-secondary">
          {title}
        </h2>
        {actions}
      </div>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

/** Centred placeholder for panels with no data yet. */
export function PanelEmpty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-4 text-center text-xs text-text-muted">
      {children}
    </div>
  );
}
