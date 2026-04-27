"use client";
import * as React from "react";
import { Icon } from "./icons";
import {
  ACCEPT_ATTR,
  filterAllowedFiles,
  inferFileType,
  mockAnalysis,
  type FileItem,
} from "./fixtures";

const FILE_TYPE: Record<
  FileItem["type"],
  { icon: () => React.ReactElement; color: string; label: string }
> = {
  pdf: { icon: () => <Icon.FileText />, color: "#ef4444", label: "PDF" },
  docx: { icon: () => <Icon.FileText />, color: "#3b82f6", label: "DOCX" },
  csv: { icon: () => <Icon.FileSheet />, color: "#10b981", label: "CSV" },
  image: { icon: () => <Icon.FileImage />, color: "#f59e0b", label: "Bild" },
};

const ENTITY_COLORS: Record<string, { bg: string; fg: string }> = {
  default: { bg: "rgba(255,255,255,.06)", fg: "var(--text-secondary)" },
  blue: { bg: "rgba(59,130,246,.12)", fg: "#93c5fd" },
  emerald: { bg: "rgba(16,185,129,.12)", fg: "#6ee7b7" },
  amber: { bg: "rgba(245,158,11,.12)", fg: "#fcd34d" },
};

function entityChipColor(type: string) {
  const map: Record<string, string> = {
    Geschäftsbereich: "blue",
    Komponente: "blue",
    Stichprobe: "blue",
    Architektur: "blue",
    Umsatz: "emerald",
    Wachstum: "emerald",
    Datenbank: "emerald",
    Kennzahl: "amber",
    Score: "amber",
    Cache: "amber",
  };
  return ENTITY_COLORS[map[type] || "default"];
}

export function ProjectFilesModal({
  projectName,
  onClose,
  files: externalFiles,
  setFiles: externalSetFiles,
  autoOpenPicker,
  onAnalysisComplete,
  notify,
  onUpload,
}: {
  projectName: string;
  onClose: () => void;
  files?: FileItem[];
  setFiles?: (updater: FileItem[] | ((prev: FileItem[]) => FileItem[])) => void;
  autoOpenPicker?: boolean;
  onAnalysisComplete?: () => void;
  notify?: (msg: string, kind?: string) => void;
  onUpload?: (files: File[]) => Promise<void> | void;
}) {
  const [internalFiles, setInternalFiles] = React.useState<FileItem[]>(
    externalFiles || []
  );
  const files = externalFiles !== undefined ? externalFiles : internalFiles;
  const setFiles = externalSetFiles || setInternalFiles;
  const [selectedId, setSelectedId] = React.useState<string | null>(
    () => (files[0] && files[0].id) || null
  );
  const [dragOver, setDragOver] = React.useState(false);
  const dragCounterRef = React.useRef(0);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const autoOpenedRef = React.useRef(false);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  React.useEffect(() => {
    if (autoOpenPicker && !autoOpenedRef.current) {
      autoOpenedRef.current = true;
      const t = setTimeout(() => {
        if (inputRef.current) inputRef.current.click();
      }, 250);
      return () => clearTimeout(t);
    }
  }, [autoOpenPicker]);

  const selected = files.find((f) => f.id === selectedId);
  const analysis = selected?.analysis;

  const addFiles = (fileList: FileList | null) => {
    const { accepted, rejected } = filterAllowedFiles(fileList);
    if (rejected.length) {
      const msg = rejected.length === 1
        ? "„" + rejected[0].name + "“ wird nicht unterstützt."
        : rejected.length + " Dateien werden nicht unterstützt.";
      if (notify) notify(msg);
    }
    if (!accepted.length) return;

    if (onUpload) {
      void onUpload(accepted);
      return;
    }

    // Fallback (no onUpload provided): simulate analysis with mock data.
    const newFiles: FileItem[] = accepted.map((f, i) => ({
      id: "f-" + Date.now() + "-" + i,
      name: f.name,
      size:
        f.size / 1024 / 1024 < 1
          ? Math.round(f.size / 1024) + " KB"
          : (f.size / 1024 / 1024).toFixed(1) + " MB",
      type: inferFileType(f.name),
      pages: 1,
      status: "analyzing",
      analysis: null,
    }));
    setFiles((prev) => [...newFiles, ...prev]);
    if (newFiles[0]) setSelectedId(newFiles[0].id);
    const ids = newFiles.map((f) => f.id);
    setTimeout(() => {
      setFiles((prev) =>
        prev.map((f) =>
          ids.includes(f.id)
            ? {
                ...f,
                status: "complete" as const,
                pages: Math.max(1, Math.round(2 + Math.random() * 14)),
                analysis: mockAnalysis(f.name),
              }
            : f
        )
      );
      if (onAnalysisComplete) onAnalysisComplete();
    }, 5000);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current = 0;
    setDragOver(false);
    addFiles(e.dataTransfer.files);
  };

  const onDragEnter = (e: React.DragEvent) => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
    e.preventDefault();
    dragCounterRef.current += 1;
    setDragOver(true);
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setDragOver(false);
  };
  const onDragOverHandler = (e: React.DragEvent) => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  return (
    <div
      className={
        "pf-overlay fixed inset-0 bg-black/60 flex items-center justify-center z-[200] p-6 " +
        "animate-[pf-fade_.15s_ease-out]" +
        (dragOver ? " is-dragging" : "")
      }
      onClick={onClose}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOverHandler}
      onDrop={onDrop}
    >
      <div
        className="pf-modal relative w-full max-w-[1080px] h-full max-h-[720px] bg-bg border border-border rounded-[14px] flex flex-col overflow-hidden shadow-[0_24px_64px_rgba(0,0,0,.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center px-5 py-4 border-b border-border flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="font-display text-base font-semibold text-text">Projektdateien</div>
            <div className="text-xs text-text-tertiary mt-0.5">{projectName}</div>
          </div>
          <button
            className="w-8 h-8 rounded-md bg-transparent border-none text-text-secondary cursor-pointer flex items-center justify-center transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text"
            onClick={onClose}
            title="Schließen"
          >
            <Icon.XBig />
          </button>
        </div>

        <div className="flex-1 grid grid-cols-[300px_1fr] min-h-0">
          <div className="border-r border-border flex flex-col min-h-0">
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPT_ATTR}
              style={{ display: "none" }}
              onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
            />

            <div className="pt-2.5 pb-1.5 px-4 text-[10px] font-semibold text-text-tertiary tracking-[0.08em] uppercase">
              Dateien ({files.length})
            </div>
            <div className="flex-1 overflow-y-auto pb-2">
              {files.length === 0 && (
                <div className="py-6 px-4 text-center text-xs text-text-tertiary tracking-[0.01em]">Noch keine Dateien</div>
              )}
              {files.map((file) => {
                const cfg = FILE_TYPE[file.type];
                const isSel = file.id === selectedId;
                return (
                  <button
                    key={file.id}
                    className={
                      "flex items-center gap-2.5 w-full px-4 py-2.5 bg-transparent border-none border-l-2 text-left cursor-pointer transition-[background-color,color] duration-150 " +
                      (isSel
                        ? "bg-bg-elevated text-text border-l-accent"
                        : "border-l-transparent text-text-secondary hover:bg-bg-hover hover:text-text")
                    }
                    onClick={() => setSelectedId(file.id)}
                  >
                    <span className="flex-shrink-0 flex items-center" style={{ color: cfg.color }}>
                      {cfg.icon()}
                    </span>
                    <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <span
                        className={
                          "text-[12.5px] text-inherit whitespace-nowrap overflow-hidden text-ellipsis " +
                          (isSel ? "font-medium" : "")
                        }
                      >
                        {file.name}
                      </span>
                      <span className="text-[11px] text-text-tertiary">
                        {(() => {
                          if (file.status === "failed") return "Fehlgeschlagen";
                          if (file.status === "complete") {
                            const c = file.chunkCount;
                            return c
                              ? `${c} ${c === 1 ? "Chunk" : "Chunks"} indiziert`
                              : `${file.size} · ${file.pages} ${file.pages === 1 ? "Seite" : "Seiten"}`;
                          }
                          switch (file.ingestStatus) {
                            case "uploading":
                              return "Hochladen…";
                            case "parsing":
                              return "Layout wird analysiert…";
                            case "embedding":
                              return "Chunks werden eingebettet…";
                            default:
                              return `${file.size} · ${file.pages} ${file.pages === 1 ? "Seite" : "Seiten"}`;
                          }
                        })()}
                      </span>
                    </span>
                    {file.status === "complete" && (
                      <span className="flex-shrink-0 flex items-center justify-center text-[#10b981]">
                        <Icon.CheckCircle />
                      </span>
                    )}
                    {file.status === "analyzing" && (
                      <span className="pf-item-badge dot" />
                    )}
                    {file.status === "failed" && (
                      <span
                        className="flex-shrink-0 flex items-center justify-center text-[#ef4444]"
                        title={file.ingestError || "Ingestion fehlgeschlagen"}
                      >
                        <Icon.XBig />
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div className="p-2.5 border-t border-border">
              <button
                type="button"
                className="w-full inline-flex items-center justify-center gap-2 px-3 py-2.5 bg-transparent border border-dashed border-border-strong rounded-[8px] text-text-secondary text-[13px] font-medium [font-family:inherit] cursor-pointer transition-[background-color,border-color,color] duration-150 hover:bg-bg-hover hover:border-accent hover:text-text [&_svg]:w-3.5 [&_svg]:h-3.5"
                onClick={() => inputRef.current && inputRef.current.click()}
              >
                <Icon.PlusBig />
                Dateien hinzufügen
              </button>
            </div>
          </div>

          <div className="overflow-y-auto flex flex-col">
            {selected && analysis ? (
              <div className="flex flex-col">
                <div className="flex items-center gap-3 px-5 py-3.5 border-b border-border">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-text whitespace-nowrap overflow-hidden text-ellipsis">{selected.name}</div>
                    <div className="text-xs text-text-tertiary mt-0.5">
                      {FILE_TYPE[selected.type].label} · {selected.size} · {selected.pages}{" "}
                      {selected.pages === 1 ? "Seite" : "Seiten"}
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1.5 h-6 px-2.5 rounded-full bg-[rgba(16,185,129,.12)] text-[#6ee7b7] text-[11px] font-medium flex-shrink-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#10b981]" />
                    Analysiert
                  </span>
                </div>

                <div className="px-5 py-3.5 border-b border-border">
                  <div className="text-[11px] font-medium text-text-tertiary mb-2">Zusammenfassung</div>
                  <p className="m-0 text-[13.5px] leading-[1.6] text-text">{analysis.summary}</p>
                </div>

                <div className="grid grid-cols-3 border-b border-border">
                  {analysis.keyStats.map((s) => (
                    <div
                      key={s.label}
                      className="px-4 py-2.5 border-r border-b border-border [&:nth-child(3n)]:border-r-0 [&:nth-child(n+4)]:border-b-0"
                    >
                      <div className="text-[10px] text-text-tertiary tracking-[0.04em]">{s.label}</div>
                      <div className="text-sm font-semibold text-text tabular-nums mt-0.5">{s.value}</div>
                    </div>
                  ))}
                </div>

                <div className="px-5 py-3.5 border-b border-border">
                  <div className="text-[11px] font-medium text-text-tertiary mb-2">
                    Erkannte Entitäten ({analysis.entities.length})
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {analysis.entities.map((e, i) => {
                      const c = entityChipColor(e.type);
                      return (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-[11.5px] font-medium"
                          style={{ background: c.bg, color: c.fg }}
                        >
                          {e.text}
                          <span className="font-mono text-[10px] opacity-60">
                            {Math.round(e.confidence * 100)}%
                          </span>
                        </span>
                      );
                    })}
                  </div>
                </div>
              </div>
            ) : selected && selected.status === "analyzing" ? (
              <div className="pf-skeleton">
                <div className="pf-skel-head">
                  <div className="pf-skel-text">
                    <div className="pf-skel-line w-60" />
                    <div className="pf-skel-line w-30 sm" />
                  </div>
                  <div className="pf-skel-pill">
                    <span className="pf-skel-dot-pulse" />
                    Analysiere…
                  </div>
                </div>
                <div className="pf-skel-block">
                  <div className="pf-skel-label" />
                  <div className="pf-skel-line w-100" />
                  <div className="pf-skel-line w-95" />
                  <div className="pf-skel-line w-80" />
                </div>
                <div className="pf-skel-stats">
                  <div className="pf-skel-stat" />
                  <div className="pf-skel-stat" />
                  <div className="pf-skel-stat" />
                </div>
                <div className="pf-skel-block">
                  <div className="pf-skel-label" />
                  <div className="pf-skel-chips">
                    <span className="pf-skel-chip w-80" />
                    <span className="pf-skel-chip w-60" />
                    <span className="pf-skel-chip w-90" />
                    <span className="pf-skel-chip w-70" />
                    <span className="pf-skel-chip w-50" />
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center gap-2.5 p-10 text-text-tertiary">
                <Icon.FileText />
                <div className="text-xs">Datei auswählen, um die Analyse zu sehen</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {dragOver && (
        <div className="fixed inset-0 bg-[rgba(13,13,13,.78)] [backdrop-filter:blur(10px)] [-webkit-backdrop-filter:blur(10px)] flex items-center justify-center z-[200] pointer-events-none animate-[pf-fade_.12s_ease-out]">
          <div className="flex flex-col items-center gap-4 px-14 py-9 border-2 border-dashed border-accent rounded-[18px] bg-white/[.02] [&_svg]:w-10 [&_svg]:h-10 [&_svg]:text-accent">
            <Icon.UploadCloud />
            <div className="font-display text-[18px] font-semibold text-text tracking-[-.01em]">Dateien hier ablegen</div>
          </div>
        </div>
      )}
    </div>
  );
}
