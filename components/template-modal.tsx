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

const TPL_BTN_BASE =
  "px-3.5 py-2 rounded-[8px] text-[13px] font-medium [font-family:inherit] " +
  "border border-transparent transition-[background-color,border-color,color,opacity] duration-150";

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
    <div
      className="fixed inset-0 bg-black/55 [backdrop-filter:blur(4px)] [-webkit-backdrop-filter:blur(4px)] z-[180] flex items-center justify-center p-6 animate-[tpl-fade_.12s_ease-out]"
      onClick={handleClose}
    >
      <div
        className="w-[min(720px,100%)] max-h-[min(720px,calc(100vh-48px))] bg-bg-elevated text-text border border-border rounded-[14px] shadow-[0_30px_80px_rgba(0,0,0,.55),0_4px_16px_rgba(0,0,0,.4)] flex flex-col overflow-hidden animate-[tpl-pop_.15s_ease-out]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Vorlage Projektanalyse"
      >
        <div className="flex items-start gap-3 px-[22px] pt-[18px] pb-3.5 border-b border-border">
          <div className="flex-1 min-w-0">
            <div className="font-display text-[17px] font-semibold tracking-[-0.01em] text-text">Vorlage · Projektanalyse</div>
            <div className="mt-1 text-[12.5px] text-text-tertiary leading-[1.5]">
              {lineCount} {lineCount === 1 ? "Frage" : "Fragen"}
              {" · wird beim Befehl "}
              <span className="inline-block px-1.5 py-px rounded-[4px] bg-accent-soft text-accent font-mono text-[11.5px] font-medium">
                Projektanalyse erstellen
              </span>
              {" als Batch an den Agent gesendet"}
            </div>
          </div>
          <button
            className="bg-transparent border-none text-text-tertiary w-7 h-7 rounded-md inline-flex items-center justify-center flex-shrink-0 transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text"
            onClick={handleClose}
            aria-label="Schließen"
          >
            <Icon.XBig />
          </button>
        </div>

        <div className="flex-1 flex flex-col gap-2.5 px-[22px] pt-4 pb-1.5 overflow-hidden min-h-0">
          <div className="text-[12.5px] text-text-tertiary leading-[1.5]">
            Eine Frage pro Zeile. Nummerierungen werden beibehalten. Du kannst Fragen ergänzen, umformulieren oder entfernen.
          </div>
          <textarea
            ref={taRef}
            className="flex-1 min-h-[280px] w-full resize-y bg-bg text-text border border-border rounded-[10px] px-3.5 py-3.5 font-mono text-[13px] leading-[1.65] [outline:none] transition-[border-color,background-color] duration-150 focus:border-border-strong focus:bg-bg-elevated"
            value={text}
            spellCheck={false}
            onChange={(e) => { setText(e.target.value); setDirty(true); }}
            placeholder={"1. Erste Frage…\n2. Zweite Frage…"}
          />
        </div>

        <div className="flex items-center gap-2 px-[22px] pt-3.5 pb-[18px] border-t border-border">
          <button
            className={TPL_BTN_BASE + " bg-transparent text-text-tertiary hover:text-text hover:bg-bg-hover"}
            onClick={handleReset}
          >
            Auf Standard zurücksetzen
          </button>
          <div className="ml-auto flex gap-2">
            <button
              className={TPL_BTN_BASE + " bg-transparent text-text-secondary border-border hover:bg-bg-hover hover:border-border-strong hover:text-text"}
              onClick={handleClose}
            >
              Abbrechen
            </button>
            <button
              className={TPL_BTN_BASE + " bg-accent text-white border-accent hover:bg-accent-hover hover:border-accent-hover disabled:opacity-45 disabled:cursor-not-allowed"}
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
