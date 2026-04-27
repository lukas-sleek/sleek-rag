"use client";
import * as React from "react";
import { Icon } from "./icons";
import type { Citation } from "./fixtures";
import { createClient } from "@/lib/supabase/client";

export function PdfViewerDialog({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose: () => void;
}) {
  const [url, setUrl] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!citation) return;
    let cancelled = false;
    setUrl(null);
    setError(null);
    const supabase = createClient();
    (async () => {
      const { data: row, error: rowErr } = await supabase
        .from("project_files")
        .select("gcs_blob_path")
        .eq("id", citation.file_id)
        .single();
      if (cancelled) return;
      if (rowErr || !row?.gcs_blob_path) {
        setError("Quelle nicht gefunden");
        return;
      }
      const { data: sig, error: sigErr } = await supabase.storage
        .from("project-files")
        .createSignedUrl(row.gcs_blob_path, 600);
      if (cancelled) return;
      if (sigErr || !sig?.signedUrl) {
        setError("Datei kann nicht geladen werden");
        return;
      }
      setUrl(`${sig.signedUrl}#page=${citation.page_start}`);
    })();
    return () => {
      cancelled = true;
    };
  }, [citation]);

  React.useEffect(() => {
    if (!citation) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [citation, onClose]);

  if (!citation) return null;

  const pageLabel =
    citation.page_start === citation.page_end
      ? `p.${citation.page_start}`
      : `p.${citation.page_start}-${citation.page_end}`;

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-[200] p-6 animate-[pf-fade_.15s_ease-out]"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-[1080px] h-full max-h-[820px] bg-bg border border-border rounded-[14px] flex flex-col overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center px-5 py-3.5 border-b border-border flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium text-text whitespace-nowrap overflow-hidden text-ellipsis">
              {citation.filename}
            </div>
            <div className="text-xs text-text-tertiary mt-0.5">
              {pageLabel}
              {citation.figure_label && ` · ${citation.figure_label}`}
            </div>
          </div>
          <button
            className="w-8 h-8 rounded-md bg-transparent border-none text-text-secondary cursor-pointer flex items-center justify-center transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text"
            onClick={onClose}
            title="Schließen"
          >
            <Icon.XBig />
          </button>
        </div>
        <div className="flex-1 min-h-0 bg-[#1a1a1a]">
          {error ? (
            <div className="h-full grid place-items-center text-text-tertiary text-sm">
              {error}
            </div>
          ) : url ? (
            <iframe src={url} className="w-full h-full border-0" title={citation.filename} />
          ) : (
            <div className="h-full grid place-items-center text-text-tertiary text-sm">
              Lädt…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
