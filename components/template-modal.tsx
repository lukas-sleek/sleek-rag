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
const MAX_TEMPLATES = 4;
const AUTOSAVE_DELAY = 700;

export type TemplateSummary = { id: string; name: string; questions: string[]; position: number };

type Draft = {
  key: string; // stable local identity (survives before a server id exists)
  id: string | null; // server id once persisted
  name: string;
  text: string;
  position: number;
  dirty: boolean;
  saving: boolean;
};

type SaveStatus = "idle" | "saving" | "saved" | "error";

function parseTemplateText(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*\d+[.)]\s*/, "").trim())
    .filter(Boolean);
}

function questionsToText(questions: string[]): string {
  return questions.map((q, i) => i + 1 + ". " + q).join("\n");
}

async function listTemplates(): Promise<TemplateSummary[]> {
  const res = await api("/api/templates");
  if (!res.ok) throw new Error("load failed");
  return (await res.json()) as TemplateSummary[];
}

const TPL_BTN_BASE =
  "px-3.5 py-2 rounded-[8px] text-[13px] font-medium [font-family:inherit] " +
  "border border-transparent transition-[background-color,border-color,color,opacity] duration-150";

const BTN_GHOST =
  "px-3.5 py-2 rounded-[7px] text-[13px] font-medium cursor-pointer " +
  "bg-transparent border border-border text-text " +
  "transition-[background-color,border-color] duration-150 hover:bg-bg-hover";

const BTN_DANGER =
  "px-3.5 py-2 rounded-[7px] text-[13px] font-medium cursor-pointer " +
  "bg-[#d63a3a] border border-[#d63a3a] text-white " +
  "transition-[background-color,border-color] duration-150 hover:bg-[#c02f2f] hover:border-[#c02f2f]";

/** A draft is worth persisting once it has at least one question. Empty new
 *  drafts are ignored entirely; existing (already-saved) drafts still sync so
 *  edits — including clearing — stick. */
function needsSave(d: Draft): boolean {
  if (!d.dirty) return false;
  return parseTemplateText(d.text).length > 0 || d.id != null;
}

export function TemplateAnalysisModal({
  open,
  onClose,
  onSaved,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
  /** Fired after any server-side mutation (create/update/delete) so callers
   *  can refetch the template list for the composer picker. */
  onChanged?: () => void;
}) {
  const [drafts, setDrafts] = React.useState<Draft[]>([]);
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [busy, setBusy] = React.useState(false); // delete in flight
  const [status, setStatus] = React.useState<SaveStatus>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = React.useState<{ key: string; name: string } | null>(null);
  const taRef = React.useRef<HTMLTextAreaElement>(null);

  // Mirrors of state so the debounced flush / close handler read fresh values.
  const draftsRef = React.useRef<Draft[]>([]);
  draftsRef.current = drafts;
  const keyCounter = React.useRef(0);
  const saveTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushing = React.useRef(false);
  const changedSinceOpen = React.useRef(false);

  const selected = drafts.find((d) => d.key === selectedKey) || null;

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listTemplates();
      const ds: Draft[] = list.map((t) => ({
        key: `srv-${t.id}`,
        id: t.id,
        name: t.name,
        text: questionsToText(t.questions),
        position: t.position,
        dirty: false,
        saving: false,
      }));
      setDrafts(ds);
      setSelectedKey(ds.length ? ds[0].key : null);
    } catch {
      setError("Vorlagen konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (!open) return;
    changedSinceOpen.current = false;
    setStatus("idle");
    load();
  }, [open, load]);

  // Persist every dirty draft worth saving. Empty new drafts are skipped
  // (ignored). Returns true if anything was written.
  const flush = React.useCallback(async (): Promise<boolean> => {
    if (flushing.current) return false;
    const pending = draftsRef.current.filter(needsSave);
    if (pending.length === 0) return false;
    flushing.current = true;
    setStatus("saving");
    let wrote = false;
    let failed = false;
    for (const d of pending) {
      const questions = parseTemplateText(d.text);
      const name = d.name.trim() || `Vorlage ${d.position + 1}`;
      setDrafts((ds) => ds.map((x) => (x.key === d.key ? { ...x, saving: true } : x)));
      try {
        let savedId = d.id;
        let savedPos = d.position;
        if (d.id == null) {
          const res = await api("/api/templates", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, questions }),
          });
          if (!res.ok) throw new Error("create failed");
          const t = (await res.json()) as TemplateSummary;
          savedId = t.id;
          savedPos = t.position;
        } else {
          const res = await api(`/api/templates/${d.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, questions }),
          });
          if (!res.ok) throw new Error("update failed");
        }
        wrote = true;
        // Clear dirty only if the draft wasn't edited again while saving.
        setDrafts((ds) =>
          ds.map((x) => {
            if (x.key !== d.key) return x;
            const stillSame = x.text === d.text && x.name === d.name;
            return {
              ...x,
              id: savedId,
              position: savedPos,
              saving: false,
              dirty: stillSame ? false : x.dirty,
            };
          })
        );
      } catch {
        failed = true;
        setDrafts((ds) => ds.map((x) => (x.key === d.key ? { ...x, saving: false } : x)));
      }
    }
    flushing.current = false;
    if (wrote) {
      changedSinceOpen.current = true;
      onChanged && onChanged();
    }
    setStatus(failed ? "error" : "saved");
    if (failed) setError("Automatisches Speichern fehlgeschlagen.");
    // If new edits arrived during the flush, persist them too.
    if (draftsRef.current.some(needsSave)) {
      scheduleSave();
    }
    return wrote;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onChanged]);

  const scheduleSave = React.useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveTimer.current = null;
      flush();
    }, AUTOSAVE_DELAY);
  }, [flush]);

  const patchDraft = (key: string, patch: Partial<Draft>) => {
    setError(null);
    setStatus("idle");
    setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, ...patch, dirty: true } : d)));
    scheduleSave();
  };

  const handleAdd = () => {
    if (drafts.length >= MAX_TEMPLATES || busy) return;
    const used = new Set(drafts.map((d) => d.position));
    let position = 0;
    while (used.has(position) && position < MAX_TEMPLATES) position++;
    const key = `new-${keyCounter.current++}`;
    setDrafts((ds) => [
      ...ds,
      { key, id: null, name: `Vorlage ${position + 1}`, text: "", position, dirty: false, saving: false },
    ]);
    setSelectedKey(key);
    setError(null);
    setTimeout(() => taRef.current && taRef.current.focus(), 60);
  };

  const dropDraft = (key: string) => {
    setDrafts((ds) => {
      const rest = ds.filter((x) => x.key !== key);
      setSelectedKey((sel) => (sel === key ? (rest.length ? rest[0].key : null) : sel));
      return rest;
    });
  };

  // Trash icon → open the in-app confirm. A never-saved empty draft has nothing
  // to lose, so it's dropped immediately without a dialog.
  const requestDelete = (key: string) => {
    if (busy) return;
    const d = drafts.find((x) => x.key === key);
    if (!d) return;
    const empty = parseTemplateText(d.text).length === 0;
    if (d.id == null && empty) {
      dropDraft(key);
      return;
    }
    setConfirmDelete({ key, name: d.name || "Unbenannt" });
  };

  const confirmDeleteNow = async () => {
    if (!confirmDelete) return;
    const { key } = confirmDelete;
    const d = drafts.find((x) => x.key === key);
    setConfirmDelete(null);
    if (!d) return;
    if (d.id == null) {
      dropDraft(key);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await api(`/api/templates/${d.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) {
        setError("Loeschen fehlgeschlagen.");
        return;
      }
      dropDraft(key);
      changedSinceOpen.current = true;
      onChanged && onChanged();
    } catch {
      setError("Loeschen fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  };

  const handleClose = React.useCallback(async () => {
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const wrote = await flush();
    if (wrote && onSaved) onSaved();
    onClose();
  }, [flush, onSaved, onClose]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (confirmDelete) {
        if (e.key === "Escape") setConfirmDelete(null);
        if (e.key === "Enter") confirmDeleteNow();
        return;
      }
      if (e.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, handleClose, confirmDelete]);

  if (!open) return null;

  const lineCount = selected
    ? selected.text.split("\n").map((l) => l.trim()).filter(Boolean).length
    : 0;

  const statusText =
    status === "saving"
      ? "Wird gespeichert…"
      : status === "saved"
      ? "Alle Aenderungen gespeichert"
      : status === "error"
      ? "Speichern fehlgeschlagen"
      : "Aenderungen werden automatisch gespeichert";

  return (
    <>
    <div
      className="fixed inset-0 bg-black/55 [backdrop-filter:blur(4px)] [-webkit-backdrop-filter:blur(4px)] z-[180] flex items-center justify-center p-6 animate-[tpl-fade_.12s_ease-out]"
      onClick={handleClose}
    >
      <div
        className="w-[min(860px,100%)] max-h-[min(720px,calc(100vh-48px))] bg-bg-elevated text-text border border-border rounded-[14px] shadow-[0_30px_80px_rgba(0,0,0,.55),0_4px_16px_rgba(0,0,0,.4)] flex flex-col overflow-hidden animate-[tpl-pop_.15s_ease-out]"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Vorlagen"
      >
        <div className="flex items-start gap-3 px-[22px] pt-[18px] pb-3.5 border-b border-border">
          <div className="flex-1 min-w-0">
            <div className="font-display text-[17px] font-semibold tracking-[-0.01em] text-text">Vorlagen</div>
            <div className="mt-1 text-[12.5px] text-text-tertiary leading-[1.5]">
              Bis zu {MAX_TEMPLATES} Vorlagen · jede wird als Batch an den Agent gesendet, wenn du sie im Chat auswählst.
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

        <div className="flex-1 flex min-h-0 overflow-hidden">
          {/* Left rail: template list */}
          <div className="w-[230px] flex-shrink-0 border-r border-border flex flex-col py-2.5 px-2 gap-1 overflow-y-auto">
            {drafts.map((d) => {
              const empty = parseTemplateText(d.text).length === 0;
              return (
                <div
                  key={d.key}
                  className={
                    "group flex items-center gap-2 px-2.5 py-2 rounded-[8px] cursor-pointer transition-[background-color] duration-100 " +
                    (d.key === selectedKey ? "bg-bg-hover" : "hover:bg-bg-hover/60")
                  }
                  onClick={() => setSelectedKey(d.key)}
                >
                  <span className="inline-flex items-center justify-center text-accent flex-shrink-0"><Icon.FileText /></span>
                  <span className="flex-1 min-w-0 text-[13px] font-medium text-text whitespace-nowrap overflow-hidden text-ellipsis">
                    {d.name || "Unbenannt"}
                  </span>
                  {empty && (
                    <span className="text-[10px] text-text-tertiary font-medium flex-shrink-0" title="Leer — wird nicht im Chat angeboten">leer</span>
                  )}
                  <button
                    className="opacity-0 group-hover:opacity-100 inline-flex items-center justify-center w-6 h-6 rounded-md text-text-tertiary hover:bg-bg-input hover:text-red-400 transition-[opacity,background-color,color] duration-150 flex-shrink-0"
                    onClick={(e) => { e.stopPropagation(); requestDelete(d.key); }}
                    title="Vorlage loeschen"
                    aria-label="Vorlage loeschen"
                    disabled={busy}
                  >
                    <Icon.Trash />
                  </button>
                </div>
              );
            })}

            {drafts.length < MAX_TEMPLATES && (
              <button
                className="mt-1 flex items-center gap-2 px-2.5 py-2 rounded-[8px] bg-transparent border border-dashed border-border text-text-secondary text-[13px] font-medium transition-[background-color,border-color,color] duration-150 hover:bg-bg-hover hover:border-border-strong hover:text-text disabled:opacity-50"
                onClick={handleAdd}
                disabled={busy || loading}
              >
                <Icon.Plus /> Vorlage hinzufuegen
              </button>
            )}

            {!loading && drafts.length === 0 && (
              <div className="px-2.5 py-2 text-[12.5px] text-text-tertiary leading-[1.5]">
                Noch keine Vorlage. Lege eine an, um sie im Chat auszuwaehlen.
              </div>
            )}
          </div>

          {/* Right pane: editor */}
          <div className="flex-1 flex flex-col min-w-0 px-[22px] pt-4 pb-1.5 overflow-hidden">
            {selected ? (
              <>
                <input
                  className="w-full bg-bg text-text border border-border rounded-[10px] px-3.5 py-2.5 text-[14px] font-medium [outline:none] transition-[border-color,background-color] duration-150 focus:border-border-strong focus:bg-bg-elevated"
                  value={selected.name}
                  placeholder="Name der Vorlage"
                  maxLength={120}
                  disabled={loading}
                  onChange={(e) => patchDraft(selected.key, { name: e.target.value })}
                />
                <div className="mt-2.5 text-[12.5px] text-text-tertiary leading-[1.5]">
                  {lineCount} {lineCount === 1 ? "Frage" : "Fragen"} · eine Frage pro Zeile. Nummerierungen werden beibehalten.
                  {lineCount === 0 && " Leere Vorlagen werden nicht gespeichert."}
                </div>
                <textarea
                  ref={taRef}
                  className="mt-2 flex-1 min-h-[260px] w-full resize-none bg-bg text-text border border-border rounded-[10px] px-3.5 py-3.5 font-mono text-[13px] leading-[1.65] [outline:none] transition-[border-color,background-color] duration-150 focus:border-border-strong focus:bg-bg-elevated disabled:opacity-50"
                  value={selected.text}
                  spellCheck={false}
                  disabled={loading}
                  onChange={(e) => patchDraft(selected.key, { text: e.target.value })}
                  placeholder={"1. Erste Frage…\n2. Zweite Frage…"}
                />
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-[13px] text-text-tertiary">
                {loading ? "Vorlagen werden geladen…" : "Keine Vorlage ausgewaehlt."}
              </div>
            )}
            {error && <div className="mt-2 text-[12.5px] text-red-400">{error}</div>}
          </div>
        </div>

        <div className="flex items-center gap-2 px-[22px] pt-3.5 pb-[18px] border-t border-border">
          {selected && (
            <button
              className={TPL_BTN_BASE + " bg-transparent text-text-tertiary hover:text-text hover:bg-bg-hover disabled:opacity-50"}
              onClick={() => patchDraft(selected.key, { text: FALLBACK_TEXT })}
              disabled={loading}
            >
              Standardfragen einfuegen
            </button>
          )}
          <div className="ml-auto flex items-center gap-3">
            <span
              className={
                "text-[12px] tabular-nums " +
                (status === "error" ? "text-red-400" : "text-text-tertiary")
              }
              aria-live="polite"
            >
              {statusText}
            </span>
            <button
              className={TPL_BTN_BASE + " bg-accent text-white border-accent hover:bg-accent-hover hover:border-accent-hover disabled:opacity-45"}
              onClick={handleClose}
            >
              Fertig
            </button>
          </div>
        </div>
      </div>
    </div>

    {confirmDelete && (
      <div
        className="fixed inset-0 bg-[rgba(15,18,22,0.42)] [backdrop-filter:blur(2px)] flex items-center justify-center z-[200] animate-[fadeIn_.14s_ease-out]"
        onClick={() => setConfirmDelete(null)}
      >
        <div
          className="bg-bg-elevated text-text border border-border rounded-[12px] shadow-[0_18px_50px_rgba(0,0,0,.18),0_4px_12px_rgba(0,0,0,.08)] pt-[22px] px-[22px] pb-[18px] w-full max-w-[400px] animate-[dialogIn_.16s_ease-out]"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
        >
          <div className="text-base font-semibold text-text mb-2">Vorlage löschen</div>
          <div className="text-[13.5px] text-text-secondary leading-[1.5] mb-[18px]">
            Vorlage <strong className="text-text font-semibold">«{confirmDelete.name}»</strong> wird endgültig gelöscht. Das kann nicht rückgängig gemacht werden.
          </div>
          <div className="flex justify-end gap-2">
            <button className={BTN_GHOST} onClick={() => setConfirmDelete(null)}>Abbrechen</button>
            <button className={BTN_DANGER} onClick={confirmDeleteNow} autoFocus>Löschen</button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
