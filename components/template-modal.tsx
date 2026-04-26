"use client";
import * as React from "react";
import { Icon } from "./icons";

const DEFAULT_TEMPLATE_QUESTIONS = [
  "In welcher Phase werden Ingenieurdienstleitungen angefragt?",
  "Welche Bauherren sind beteiligt?",
  "Wie heisst der Projektleiter?",
  "Welche Termine sind vorgesehen? Gibt es zwingende Meilensteine für z.B. Zwischentermine, Gleisschlagwochenenden oder ähnliche?",
  "Was ist die Bausumme?",
  "Welche Drittprojekte tangieren den Perimeter?",
  "Welche Rahmenbedingungen betreffen das Projekt hinsichtlich Termine, Bauzeit oder ähnlichem?",
  "Welche Elemente sind vom Bauprojekt zu überarbeiten? Wie viel Stunden sind dafür in der Ausschreibung vorgesehen?",
  "Welche Elemente sind im Ausführungsprojekt zu überabreiten oder zu ändern?",
  "Ist die Vermessung Bestandteil unseres Auftrags oder ist diese nur zu koordinieren?",
  "Steht in den Plänen irgendwo der Kommentar „Ist in einer späteren Phase zu Detaillieren.“ oder etwas ähnliches?",
];

const DEFAULT_TEMPLATE_TEXT = DEFAULT_TEMPLATE_QUESTIONS
  .map((q, i) => i + 1 + ". " + q)
  .join("\n");

const TEMPLATE_STORAGE_KEY = "eag-llm.projektanalyse-template";

function loadTemplate(): string {
  if (typeof window === "undefined") return DEFAULT_TEMPLATE_TEXT;
  try {
    const v = localStorage.getItem(TEMPLATE_STORAGE_KEY);
    if (v && v.trim()) return v;
  } catch {}
  return DEFAULT_TEMPLATE_TEXT;
}

function saveTemplate(text: string) {
  try {
    localStorage.setItem(TEMPLATE_STORAGE_KEY, text);
  } catch {}
}

export function TemplateAnalysisModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: (text: string) => void;
}) {
  const [text, setText] = React.useState<string>(DEFAULT_TEMPLATE_TEXT);
  const [dirty, setDirty] = React.useState(false);
  const taRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (open) {
      setText(loadTemplate());
      setDirty(false);
      const t = setTimeout(() => taRef.current && taRef.current.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [open]);

  const handleSave = React.useCallback(() => {
    saveTemplate(text);
    setDirty(false);
    if (onSaved) onSaved(text);
    onClose();
  }, [text, onSaved, onClose]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") handleSave();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, handleSave]);

  const handleReset = () => {
    setText(DEFAULT_TEMPLATE_TEXT);
    setDirty(true);
  };

  const handleClose = () => {
    if (dirty) {
      const ok = window.confirm("Ungespeicherte Änderungen verwerfen?");
      if (!ok) return;
    }
    onClose();
  };

  if (!open) return null;

  const lineCount = text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean).length;

  return (
    <div className="tpl-overlay" onClick={handleClose}>
      <div
        className="tpl-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Vorlage Projektanalyse"
      >
        <div className="tpl-header">
          <div className="tpl-header-text">
            <div className="tpl-title">Vorlage · Projektanalyse</div>
            <div className="tpl-subtitle">
              {lineCount} {lineCount === 1 ? "Frage" : "Fragen"}
              {" · wird beim Befehl "}
              <span className="tpl-cmd">Projektanalyse erstellen</span>
              {" als Batch an den Agent gesendet"}
            </div>
          </div>
          <button className="tpl-close" onClick={handleClose} aria-label="Schließen">
            <Icon.XBig />
          </button>
        </div>

        <div className="tpl-body">
          <div className="tpl-hint">
            Eine Frage pro Zeile. Nummerierungen werden beibehalten. Du kannst Fragen ergänzen, umformulieren oder entfernen.
          </div>
          <textarea
            ref={taRef}
            className="tpl-textarea"
            value={text}
            spellCheck={false}
            onChange={(e) => { setText(e.target.value); setDirty(true); }}
            placeholder="1. Erste Frage…&#10;2. Zweite Frage…"
          />
        </div>

        <div className="tpl-footer">
          <button className="tpl-btn tpl-btn-ghost" onClick={handleReset}>
            Auf Standard zurücksetzen
          </button>
          <div className="tpl-footer-right">
            <button className="tpl-btn tpl-btn-secondary" onClick={handleClose}>
              Abbrechen
            </button>
            <button
              className="tpl-btn tpl-btn-primary"
              onClick={handleSave}
              disabled={!dirty}
              title={dirty ? "Speichern (⌘↵)" : "Keine Änderungen"}
            >
              Speichern
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
