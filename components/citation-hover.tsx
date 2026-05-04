"use client";
import * as React from "react";
import { Icon } from "./icons";
import type { Citation } from "./fixtures";

// Self-contained hover popover. No Radix / Floating UI dependency — opens
// on hover or focus, anchored absolutely above the trigger and centred
// horizontally. Body is scrollable so long chunk snippets don't break the
// layout. Used by the inline [N] citation links in chat.tsx.

function confidenceTone(score: number | null | undefined) {
  if (score == null) {
    return { label: "—", className: "text-text-tertiary" };
  }
  const pct = Math.round(score * 100);
  let className = "text-rose-300";
  if (score >= 0.7) className = "text-emerald-300";
  else if (score >= 0.4) className = "text-amber-300";
  return { label: `${pct}%`, className };
}

export function CitationHover({
  citation,
  onClick,
  children,
}: {
  citation: Citation;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  // Close timer so a brief mouse traversal between trigger + card doesn't
  // pop the panel away mid-read.
  const closeTimer = React.useRef<number | null>(null);
  const cancelClose = () => {
    if (closeTimer.current != null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = window.setTimeout(() => setOpen(false), 100);
  };
  React.useEffect(() => () => cancelClose(), []);

  const tone = confidenceTone(citation.score);
  const isWeb = citation.kind === "web";
  const heading = isWeb
    ? citation.title || citation.domain || citation.url || "Web"
    : citation.filename;
  const snippet = citation.snippet ?? "";

  return (
    <span
      className="relative inline-block"
      onMouseEnter={() => {
        cancelClose();
        setOpen(true);
      }}
      onMouseLeave={scheduleClose}
      onFocus={() => {
        cancelClose();
        setOpen(true);
      }}
      onBlur={scheduleClose}
    >
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          onClick();
        }}
        className="font-mono tabular-nums text-[12px] text-accent hover:text-accent-hover underline underline-offset-2 align-baseline px-px"
      >
        {children}
      </button>
      {open && (
        <span
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          className={
            "absolute left-1/2 -translate-x-1/2 bottom-full mb-2 z-40 " +
            "w-[320px] max-w-[80vw] " +
            "rounded-lg border border-border bg-bg-elevated " +
            "shadow-[0_12px_28px_rgba(0,0,0,.45),0_2px_6px_rgba(0,0,0,.3)] " +
            "p-3 text-left cursor-default"
          }
        >
          <span className="flex items-center gap-2 mb-2">
            {isWeb ? (
              <Icon.Globe />
            ) : (
              <Icon.FileText />
            )}
            <span className="text-[12px] font-medium text-text truncate flex-1">
              {heading}
            </span>
            <span
              title="Konfidenz aus grounding_supports (höher = relevanter)"
              className={
                "font-mono tabular-nums text-[10.5px] uppercase tracking-wider " +
                tone.className
              }
            >
              {tone.label}
            </span>
          </span>
          <span className="block max-h-48 overflow-auto text-[11.5px] leading-[1.5] text-text-secondary whitespace-pre-wrap break-words">
            {snippet || (
              <span className="italic text-text-tertiary">
                Keine Vorschau verfügbar.
              </span>
            )}
          </span>
        </span>
      )}
    </span>
  );
}
