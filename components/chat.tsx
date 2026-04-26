"use client";
import * as React from "react";
import { Icon } from "./icons";
import type { Message as Msg } from "./fixtures";

const SUGGESTIONS = [
  { title: "Projektanalyse erstellen", desc: "Strukturierte Auswertung über alle Dokumente im aktiven Projekt" },
  { title: "Zusammenfassung aller Dateien im Projekt", desc: "Knappe Übersicht je Datei mit Quellenangabe" },
];

export function Message({ msg, streaming }: { msg: Msg; streaming: boolean }) {
  const isUser = msg.role === "user";
  if (isUser) {
    return (
      <div className="msg msg-user">
        <div className="msg-bubble">{msg.content}</div>
      </div>
    );
  }
  return (
    <div className="msg msg-ai">
      <div className="msg-content">{msg.content}</div>
      {!streaming && (
        <div className="msg-actions">
          <button className="msg-action"><Icon.Copy /> Kopieren</button>
          <button className="msg-action">↻ Neu generieren</button>
        </div>
      )}
    </div>
  );
}

export function EmptyState({
  onSuggest,
  hasFiles = true,
  projectName,
  onAddFiles,
  userName = "Alex",
}: {
  onSuggest: (title: string) => void;
  hasFiles?: boolean;
  projectName?: string;
  onAddFiles?: () => void;
  userName?: string;
}) {
  if (!hasFiles) {
    return (
      <div className="empty">
        <div className="empty-mark">
          EAG <span className="accent">LLM</span>
        </div>
        <div className="empty-greeting">Hallo, {userName}.</div>
        <div className="empty-sub">
          Lade Dokumente hoch, damit ich auf Basis deiner Projektdateien antworten kann.
        </div>
        <button className="empty-cta" onClick={() => onAddFiles && onAddFiles()}>
          <Icon.Paperclip /> Dateien hinzufügen
        </button>
        <div className="empty-hint">Unterstützte Formate: PDF, Office, Bild, TXT, MD, CSV</div>
      </div>
    );
  }
  return (
    <div className="empty">
      <div className="empty-mark">
        EAG <span className="accent">LLM</span>
      </div>
      <div className="empty-greeting">
        Hallo, {userName}. <span className="muted">Was kann ich für dich heraussuchen?</span>
      </div>
      <div className="suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s.title} className="suggestion" onClick={() => onSuggest(s.title)}>
            <div className="title">{s.title}</div>
            <div className="desc">{s.desc}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

const MODELS = [
  { id: "gpt-4o", name: "GPT-4o", provider: "OpenAI", maxTokens: "128k" },
  { id: "claude-3.5-sonnet", name: "Claude 3.5 Sonnet", provider: "Anthropic", maxTokens: "200k" },
  { id: "gemini-1.5-pro", name: "Gemini 1.5 Pro", provider: "Google", maxTokens: "1M" },
  { id: "llama-3.1-70b", name: "Llama 3.1 70B", provider: "Meta", maxTokens: "128k" },
];

function Dropdown({
  trigger,
  children,
  align = "start",
}: {
  trigger: React.ReactElement<{ onClick?: (e: React.MouseEvent) => void }>;
  children:
    | React.ReactNode
    | ((args: { close: () => void }) => React.ReactNode);
  align?: "start" | "end";
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDoc);
    return () => window.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="cmp-dd" ref={ref}>
      {React.cloneElement(trigger, {
        onClick: (e: React.MouseEvent) => {
          e.stopPropagation();
          setOpen((v) => !v);
        },
      })}
      {open && (
        <div className={"cmp-dd-menu align-" + align} onClick={(e) => e.stopPropagation()}>
          {typeof children === "function" ? children({ close: () => setOpen(false) }) : children}
        </div>
      )}
    </div>
  );
}

type Attachment = { id: string; name: string; size: string };

export function Composer({
  onSend,
  streaming,
  onStop,
}: {
  onSend: (text: string) => void;
  streaming: boolean;
  onStop: () => void;
}) {
  const [value, setValue] = React.useState("");
  const [model, setModel] = React.useState(MODELS[0]);
  const [temp, setTemp] = React.useState(0.7);
  const [showSettings, setShowSettings] = React.useState(false);
  const [attachments, setAttachments] = React.useState<Attachment[]>([]);
  const ref = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (!ref.current) return;
    ref.current.style.height = "auto";
    ref.current.style.height = Math.min(ref.current.scrollHeight, 208) + "px";
  }, [value]);

  const submit = () => {
    const v = value.trim();
    if (!v || streaming) return;
    onSend(v);
    setValue("");
    setAttachments([]);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const addFile = () => {
    const n = attachments.length + 1;
    setAttachments((a) => [
      ...a,
      { id: "f" + Date.now() + n, name: `dokument-${n}.pdf`, size: "2.4 MB" },
    ]);
  };

  const removeFile = (id: string) =>
    setAttachments((a) => a.filter((f) => f.id !== id));

  return (
    <div className="composer-wrap">
      <form
        className="composer-card"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        {attachments.length > 0 && (
          <div className="cmp-attach-row">
            {attachments.map((f) => (
              <span key={f.id} className="cmp-chip">
                <Icon.File />
                <span className="cmp-chip-name">{f.name}</span>
                <span className="cmp-chip-size">{f.size}</span>
                <button
                  type="button"
                  className="cmp-chip-x"
                  onClick={() => removeFile(f.id)}
                  aria-label="Anhang entfernen"
                >
                  <Icon.X />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="cmp-textarea-wrap">
          <textarea
            ref={ref}
            rows={1}
            placeholder="Frag etwas…"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKey}
          />
        </div>

        {showSettings && (
          <div className="cmp-settings">
            <div className="cmp-settings-row">
              <span className="cmp-settings-label">Temperature</span>
              <span className="cmp-settings-value">{temp.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min="0"
              max="2"
              step="0.01"
              value={temp}
              onChange={(e) => setTemp(parseFloat(e.target.value))}
              className="cmp-slider"
            />
            <div className="cmp-settings-ends">
              <span>Präzise</span>
              <span>Kreativ</span>
            </div>
          </div>
        )}

        <div className="cmp-toolbar">
          <Dropdown
            trigger={
              <button type="button" className="cmp-iconbtn" title="Hinzufügen">
                <Icon.Plus />
              </button>
            }
          >
            {({ close }) => (
              <>
                <button type="button" className="cmp-menu-item" onClick={() => { addFile(); close(); }}>
                  <Icon.Paperclip /> Dateien hinzufügen
                </button>
                <button type="button" className="cmp-menu-item" onClick={close}>
                  <Icon.Sparkles /> Agent-Modus
                </button>
                <button type="button" className="cmp-menu-item" onClick={close}>
                  <Icon.SearchSm /> Deep Research
                </button>
              </>
            )}
          </Dropdown>

          <Dropdown
            trigger={
              <button type="button" className="cmp-modelbtn" title="Modell wählen">
                {model.name}
                <Icon.ChevronDownSm />
              </button>
            }
          >
            {({ close }) => (
              <>
                {MODELS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={"cmp-menu-item cmp-model-item" + (m.id === model.id ? " active" : "")}
                    onClick={() => { setModel(m); close(); }}
                  >
                    <span className="cmp-model-name">
                      <span>{m.name}</span>
                      <span className="cmp-model-provider">{m.provider}</span>
                    </span>
                    <span className="cmp-model-tokens">{m.maxTokens}</span>
                  </button>
                ))}
              </>
            )}
          </Dropdown>

          <button
            type="button"
            className={"cmp-iconbtn" + (showSettings ? " is-active" : "")}
            onClick={() => setShowSettings((v) => !v)}
            title="Einstellungen"
          >
            <Icon.Sliders />
          </button>

          <div className="cmp-toolbar-right">
            {attachments.length > 0 && (
              <span className="cmp-filecount">
                {attachments.length} {attachments.length === 1 ? "Datei" : "Dateien"}
              </span>
            )}
            <button type="button" className="cmp-iconbtn" title="Spracheingabe">
              <Icon.Mic />
            </button>
            {streaming ? (
              <button type="button" className="cmp-send" onClick={onStop} title="Generierung stoppen">
                <Icon.Stop />
              </button>
            ) : (
              value.trim() && (
                <button type="submit" className="cmp-send" title="Senden">
                  <Icon.ArrowUp />
                </button>
              )
            )}
          </div>
        </div>
      </form>
      <div className="composer-hint">
        EAG LLM greift auf deine indexierten Quellen zu. Wichtige Antworten bitte verifizieren.
      </div>
    </div>
  );
}
