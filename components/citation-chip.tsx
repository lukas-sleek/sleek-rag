"use client";
import { Icon } from "./icons";
import type { Citation } from "./fixtures";

export function CitationChip({
  citation,
  index,
  onClick,
}: {
  citation: Citation;
  index: number;
  onClick: () => void;
}) {
  const isWeb = citation.kind === "web";
  if (isWeb) {
    const label = citation.domain || citation.url || "Web";
    const tooltip = citation.title || citation.url || label;
    return (
      <button
        type="button"
        onClick={onClick}
        title={tooltip}
        className={
          "inline-flex items-center gap-1 px-2 h-6 rounded-full text-[11px] font-medium " +
          "bg-bg-input border border-border text-text-secondary cursor-pointer " +
          "transition-[background-color,color,border-color] duration-150 " +
          "hover:bg-bg-hover hover:text-text hover:border-border-strong " +
          "[&_svg]:w-3 [&_svg]:h-3 [&_svg]:flex-shrink-0 [&_svg]:text-text-tertiary " +
          "hover:[&_svg]:text-text-secondary"
        }
      >
        <Icon.Globe />
        <span className="font-mono tabular-nums">[{index}]</span>
        <span className="max-w-[200px] overflow-hidden text-ellipsis whitespace-nowrap">
          {label}
        </span>
      </button>
    );
  }

  const isFigure = !!citation.figure_label;
  // page_start/page_end are nullable on the Pattern A path (plan 18.3 T8) —
  // when the LLM Parser dropped the [Seite N] marker we just omit the page
  // label rather than rendering "p.null".
  const pageLabel =
    citation.page_start == null || citation.page_end == null
      ? null
      : citation.page_start === citation.page_end
        ? `p.${citation.page_start}`
        : `p.${citation.page_start}-${citation.page_end}`;
  return (
    <button
      type="button"
      onClick={onClick}
      title={citation.snippet}
      className={
        "inline-flex items-center gap-1 px-2 h-6 rounded-full text-[11px] font-medium " +
        "bg-bg-input border border-border text-text-secondary cursor-pointer " +
        "transition-[background-color,color,border-color] duration-150 " +
        "hover:bg-bg-hover hover:text-text hover:border-border-strong " +
        "[&_svg]:w-3 [&_svg]:h-3 [&_svg]:flex-shrink-0 [&_svg]:text-text-tertiary " +
        "hover:[&_svg]:text-text-secondary"
      }
    >
      {isFigure ? <Icon.FileImage /> : <Icon.FileText />}
      <span className="font-mono tabular-nums">[{index}]</span>
      <span className="max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap">
        {citation.filename}
      </span>
      {pageLabel && <span className="text-text-tertiary">{pageLabel}</span>}
      {citation.figure_label && (
        <span className="text-text-tertiary">· {citation.figure_label}</span>
      )}
    </button>
  );
}
