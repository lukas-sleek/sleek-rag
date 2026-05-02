"use client";
import * as React from "react";
import { Icon } from "./icons";
import { api } from "@/lib/api";

const FALLBACK_QUESTIONS = [
  "In welcher Phase werden Ingenieurdienstleitungen angefragt?",
  "Welche Bauherren sind beteiligt?",
  "Wie heisst der Projektleiter?",
  "Welche Termine sind vorgesehen? Gibt es zwingende Meilensteine fuer z.B. Zwischentermine, Gleisschlagwochenenden oder aehnliche?",
  "Was ist die Bausumme?",
  "Welche Drittprojekte tangieren den Perimeter?",
  "Welche Rahmenbedingungen betreffen das Projekt hinsichtlich Termine, Bauzeit oder aehnlichem?",
  "Welche Elemente sind vom Bauprojekt zu ueberarbeiten? Wie viel Stunden sind dafuer in der Ausschreibung vorgesehen?",
  "Welche Elemente sind im Ausfuehrungsprojekt zu ueberarbeiten oder zu aendern?",
  "Ist die Vermessung Bestandteil unseres Auftrags oder ist diese nur zu koordinieren?",
  'Steht in den Plaenen irgendwo der Kommentar "Ist in einer spaeteren Phase zu Detaillieren." oder etwas aehnliches?',
];

const FALLBACK_TEXT = FALLBACK_QUESTIONS.map((q, i) => i + 1 + ". " + q).join("\n");

function parseTemplateText(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*\d+[.)]\s*/, "").trim())
    .filter(Boolean);
}

function questionsToText(questions: string[]): string {
  return questions.map((q, i) => i + 1 + ". " + q).join("\n");
}

async function fetchTemplate(): Promise<string[]> {
  try {
    const res = await api("/api/templates/projektanalyse");
    if (!res.ok) return FALLBACK_QUESTIONS;
    const data = (await res.json()) as { questions?: string[] };
    return data.questions && data.questions.length ? data.questions : FALLBACK_QUESTIONS;
  } catch {
    return FALLBACK_QUESTIONS;
  }
}

async function saveTemplate(questions: string[]): Promise<boolean> {
  try {
    const res = await api("/api/templates/projektanalyse", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ questions }),
    });
    return res.ok;
  } catch {
    return false;
  }
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
  onSaved?: () => void;
}) {
  const [text, setText] = React.useState<string>(FALLBACK_TEXT);
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const taRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTemplate().then((qs) => {
      if (cancelled) return;
      setText(questionsToText(qs));
      setDirty(false);
      setLoading(false);
      setTimeout(() => taRef.current && taRef.current.focus(), 80);
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const handleSave = React.useCallback(async () => {
    const qs = parseTemplateText(text);
    if (qs.length === 0) {
      setError("Mindestens eine Frage erforderlich.");
      return;
    }
    setSaving(true);
    setError(null);
    const ok = await saveTemplate(qs);
    setSaving(false);
    if (!ok) {
      setError("Speichern fehlgeschlagen.");
      return;
    }
    setDirty(false);
    if (onSaved) onSaved();
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
    setText(FALLBACK_TEXT);
    setDirty(true);
  };

  const handleClose = () => {
    if (dirty) {
      const ok = window.confirm("Ungespeicherte Aenderungen verwerfen?");
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
            aria-label="Schliessen"
          >
            <Icon.XBig />
          </button>
        </div>

        <div className="flex-1 flex flex-col gap-2.5 px-[22px] pt-4 pb-1.5 overflow-hidden min-h-0">
          <div className="text-[12.5px] text-text-tertiary leading-[1.5]">
            Eine Frage pro Zeile. Nummerierungen werden beibehalten. Du kannst Fragen ergaenzen, umformulieren oder entfernen.
          </div>
          <textarea
            ref={taRef}
            className="flex-1 min-h-[280px] w-full resize-y bg-bg text-text border border-border rounded-[10px] px-3.5 py-3.5 font-mono text-[13px] leading-[1.65] [outline:none] transition-[border-color,background-color] duration-150 focus:border-border-strong focus:bg-bg-elevated disabled:opacity-50"
            value={text}
            spellCheck={false}
            disabled={loading || saving}
            onChange={(e) => { setText(e.target.value); setDirty(true); }}
            placeholder={loading ? "Vorlage wird geladen…" : "1. Erste Frage…\n2. Zweite Frage…"}
          />
          {error && (
            <div className="text-[12.5px] text-red-400">{error}</div>
          )}
        </div>

        <div className="flex items-center gap-2 px-[22px] pt-3.5 pb-[18px] border-t border-border">
          <button
            className={TPL_BTN_BASE + " bg-transparent text-text-tertiary hover:text-text hover:bg-bg-hover disabled:opacity-50"}
            onClick={handleReset}
            disabled={loading || saving}
          >
            Auf Standard zuruecksetzen
          </button>
          <div className="ml-auto flex gap-2">
            <button
              className={TPL_BTN_BASE + " bg-transparent text-text-secondary border-border hover:bg-bg-hover hover:border-border-strong hover:text-text"}
              onClick={handleClose}
              disabled={saving}
            >
              Abbrechen
            </button>
            <button
              className={TPL_BTN_BASE + " bg-accent text-white border-accent hover:bg-accent-hover hover:border-accent-hover disabled:opacity-45 disabled:cursor-not-allowed"}
              onClick={handleSave}
              disabled={!dirty || loading || saving}
              title={dirty ? "Speichern (⌘↵)" : "Keine Aenderungen"}
            >
              {saving ? "Speichern…" : "Speichern"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
