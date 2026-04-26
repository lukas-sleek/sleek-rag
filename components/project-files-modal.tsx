"use client";
import * as React from "react";
import { Icon } from "./icons";
import {
  ACCEPT_ATTR,
  filterAllowedFiles,
  inferFileType,
  mockAnalysis,
  SAMPLE_FILES,
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
}: {
  projectName: string;
  onClose: () => void;
  files?: FileItem[];
  setFiles?: (updater: FileItem[] | ((prev: FileItem[]) => FileItem[])) => void;
  autoOpenPicker?: boolean;
  onAnalysisComplete?: () => void;
  notify?: (msg: string, kind?: string) => void;
}) {
  const [internalFiles, setInternalFiles] = React.useState<FileItem[]>(
    externalFiles || SAMPLE_FILES
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
      className={"pf-overlay" + (dragOver ? " is-dragging" : "")}
      onClick={onClose}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOverHandler}
      onDrop={onDrop}
    >
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-header">
          <div className="pf-header-text">
            <div className="pf-title">Projektdateien</div>
            <div className="pf-subtitle">{projectName}</div>
          </div>
          <button className="pf-close" onClick={onClose} title="Schließen">
            <Icon.XBig />
          </button>
        </div>

        <div className="pf-grid">
          <div className="pf-left">
            <input
              ref={inputRef}
              type="file"
              multiple
              accept={ACCEPT_ATTR}
              style={{ display: "none" }}
              onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
            />

            <div className="pf-list-head">Dateien ({files.length})</div>
            <div className="pf-list">
              {files.length === 0 && (
                <div className="pf-list-empty">Noch keine Dateien</div>
              )}
              {files.map((file) => {
                const cfg = FILE_TYPE[file.type];
                const isSel = file.id === selectedId;
                return (
                  <button
                    key={file.id}
                    className={"pf-item" + (isSel ? " active" : "")}
                    onClick={() => setSelectedId(file.id)}
                  >
                    <span className="pf-item-icon" style={{ color: cfg.color }}>
                      {cfg.icon()}
                    </span>
                    <span className="pf-item-text">
                      <span className="pf-item-name">{file.name}</span>
                      <span className="pf-item-meta">
                        {file.size} · {file.pages} {file.pages === 1 ? "Seite" : "Seiten"}
                      </span>
                    </span>
                    {file.status === "complete" && (
                      <span className="pf-item-badge ok"><Icon.CheckCircle /></span>
                    )}
                    {file.status === "analyzing" && (
                      <span className="pf-item-badge dot" />
                    )}
                  </button>
                );
              })}
            </div>
            <div className="pf-list-foot">
              <button
                type="button"
                className="pf-add-btn"
                onClick={() => inputRef.current && inputRef.current.click()}
              >
                <Icon.PlusBig />
                Dateien hinzufügen
              </button>
            </div>
          </div>

          <div className="pf-right">
            {selected && analysis ? (
              <div className="pf-analysis">
                <div className="pf-file-head">
                  <div className="pf-file-text">
                    <div className="pf-file-name">{selected.name}</div>
                    <div className="pf-file-meta">
                      {FILE_TYPE[selected.type].label} · {selected.size} · {selected.pages}{" "}
                      {selected.pages === 1 ? "Seite" : "Seiten"}
                    </div>
                  </div>
                  <span className="pf-status-pill">
                    <span className="pf-status-dot" />
                    Analysiert
                  </span>
                </div>

                <div className="pf-section">
                  <div className="pf-section-label">Zusammenfassung</div>
                  <p className="pf-summary">{analysis.summary}</p>
                </div>

                <div className="pf-stats">
                  {analysis.keyStats.map((s) => (
                    <div className="pf-stat" key={s.label}>
                      <div className="pf-stat-label">{s.label}</div>
                      <div className="pf-stat-value">{s.value}</div>
                    </div>
                  ))}
                </div>

                <div className="pf-section">
                  <div className="pf-section-label">
                    Erkannte Entitäten ({analysis.entities.length})
                  </div>
                  <div className="pf-chips">
                    {analysis.entities.map((e, i) => {
                      const c = entityChipColor(e.type);
                      return (
                        <span
                          key={i}
                          className="pf-chip"
                          style={{ background: c.bg, color: c.fg }}
                        >
                          {e.text}
                          <span className="pf-chip-conf">
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
              <div className="pf-empty">
                <Icon.FileText />
                <div className="pf-empty-text">Datei auswählen, um die Analyse zu sehen</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {dragOver && (
        <div className="pf-drop-overlay-full">
          <div className="pf-drop-inner">
            <Icon.UploadCloud />
            <div className="pf-drop-title">Dateien hier ablegen</div>
          </div>
        </div>
      )}
    </div>
  );
}
