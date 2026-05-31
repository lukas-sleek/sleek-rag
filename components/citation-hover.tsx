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

  const isWeb = citation.kind === "web";
  const heading = isWeb
    ? citation.title || citation.domain || citation.url || "Web"
    : citation.filename;
  // For web citations the score lives on the citation itself. For file
  // citations we now have N chunks per file (file-level dedupe), each
  // with its own score — render them as a list below instead of a single
  // header tone.
  const headerTone = isWeb ? confidenceTone(citation.score) : null;
  // Chunks list: prefer the per-chunk array populated by the dedupe
  // aggregator; fall back to the legacy single-snippet shape.
  const chunks: Array<{
    snippet?: string | null;
    score?: number | null;
    page_first?: number | null;
    page_last?: number | null;
  }> =
    citation.chunks && citation.chunks.length > 0
      ? citation.chunks
      : [
          {
            snippet: citation.snippet,
            score: citation.score,
            page_first: citation.page_first,
            page_last: citation.page_last,
          },
        ];

  function pageSuffixFor(c: {
    page_first?: number | null;
    page_last?: number | null;
  }): string {
    const pf = c.page_first ?? null;
    const pl = c.page_last ?? null;
    if (pf == null) return "";
    return pl != null && pl !== pf ? ` · S. ${pf}–${pl}` : ` · S. ${pf}`;
  }

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
            {isWeb ? <Icon.Globe /> : <Icon.FileText />}
            <span className="text-[12px] font-medium text-text truncate flex-1">
              {heading}
            </span>
            {!isWeb && chunks.length > 1 && (
              <span className="font-mono tabular-nums text-[10.5px] uppercase tracking-wider text-text-tertiary">
                {chunks.length} Treffer
              </span>
            )}
            {headerTone && (
              <span
                title="Konfidenz aus grounding_supports (höher = relevanter)"
                className={
                  "font-mono tabular-nums text-[10.5px] uppercase tracking-wider " +
                  headerTone.className
                }
              >
                {headerTone.label}
              </span>
            )}
          </span>
          <span className="block max-h-64 overflow-auto space-y-2">
            {chunks.map((c, i) => {
              const chunkTone = confidenceTone(c.score);
              const text = c.snippet || "";
              const ps = pageSuffixFor(c);
              return (
                <span
                  key={i}
                  className={
                    "block " +
                    (i > 0
                      ? "pt-2 border-t border-border"
                      : "")
                  }
                >
                  {!isWeb && (
                    <span className="flex items-center gap-2 mb-1">
                      {chunks.length > 1 && (
                        <span className="font-mono tabular-nums text-[10px] text-text-tertiary">
                          #{i + 1}
                        </span>
                      )}
                      {ps && (
                        <span className="text-[10.5px] text-text-tertiary">
                          {ps.replace(/^ · /, "")}
                        </span>
                      )}
                      <span
                        title="Konfidenz aus grounding_supports (höher = relevanter)"
                        className={
                          "ml-auto font-mono tabular-nums text-[10.5px] uppercase tracking-wider " +
                          chunkTone.className
                        }
                      >
                        {chunkTone.label}
                      </span>
                    </span>
                  )}
                  <span className="block text-[11.5px] leading-[1.5] text-text-secondary whitespace-pre-wrap break-words">
                    {text || (
                      <span className="italic text-text-tertiary">
                        Keine Vorschau verfügbar.
                      </span>
                    )}
                  </span>
                </span>
              );
            })}
          </span>
        </span>
      )}
    </span>
  );
}
