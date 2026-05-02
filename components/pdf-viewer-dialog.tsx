"use client";
import * as React from "react";
import { Icon } from "./icons";
import type { Citation } from "./fixtures";
import { api } from "@/lib/api";

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
    // PDFs live in GCS. Backend mints a V4 signed URL via
    // /api/projects/{project_id}/files/{file_id}/signed-url; the dialog
    // iframes it. Page anchors are gone — Vertex serverless retrieval
    // doesn't return page spans, so the viewer always opens at page 1.
    if (!citation.project_id || !citation.file_id) {
      setError("Quelle nicht gefunden");
      return;
    }
    (async () => {
      try {
        const res = await api(
          `/api/projects/${citation.project_id}/files/${citation.file_id}/signed-url`,
        );
        if (cancelled) return;
        if (!res.ok) {
          setError(res.status === 404 ? "Quelle nicht gefunden" : "Datei kann nicht geladen werden");
          return;
        }
        const body = (await res.json()) as { url?: string };
        if (cancelled) return;
        if (!body.url) {
          setError("Datei kann nicht geladen werden");
          return;
        }
        setUrl(body.url);
      } catch {
        if (!cancelled) setError("Datei kann nicht geladen werden");
      }
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
