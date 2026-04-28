"use client";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "./icons";
import type { Citation, Message as Msg } from "./fixtures";
import { CitationChip } from "./citation-chip";
import { createClient } from "@/lib/supabase/client";

const MD_PROSE =
  "text-[14.5px] leading-[1.65] text-text break-words " +
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 " +
  "[&_h1]:font-display [&_h1]:text-[22px] [&_h1]:font-semibold [&_h1]:tracking-[-0.02em] [&_h1]:mt-6 [&_h1]:mb-3 " +
  "[&_h2]:font-display [&_h2]:text-[17px] [&_h2]:font-semibold [&_h2]:tracking-[-0.01em] [&_h2]:mt-5 [&_h2]:mb-2 " +
  "[&_h3]:text-[15px] [&_h3]:font-semibold [&_h3]:mt-4 [&_h3]:mb-1.5 " +
  "[&_p]:my-2 [&_p:empty]:hidden " +
  "[&_ul]:my-2 [&_ul]:pl-5 [&_ul]:list-disc [&_ol]:my-2 [&_ol]:pl-5 [&_ol]:list-decimal " +
  "[&_li]:my-0.5 [&_li>p]:my-0 " +
  "[&_strong]:font-semibold [&_em]:italic " +
  "[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-accent-hover " +
  "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-text-secondary [&_blockquote]:my-2 " +
  "[&_hr]:my-4 [&_hr]:border-border " +
  "[&_code]:font-mono [&_code]:text-[13px] [&_code]:bg-bg-input [&_code]:border [&_code]:border-border [&_code]:px-1.5 [&_code]:py-px [&_code]:rounded-[4px] " +
  "[&_pre]:bg-bg-input [&_pre]:border [&_pre]:border-border [&_pre]:rounded-md [&_pre]:p-3 [&_pre]:overflow-x-auto [&_pre]:my-3 [&_pre_code]:bg-transparent [&_pre_code]:border-0 [&_pre_code]:p-0 " +
  "[&_table]:border-collapse [&_table]:my-3 [&_table]:text-[13.5px] " +
  "[&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold [&_th]:bg-bg-input " +
  "[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_td]:align-top";

const SUGGESTIONS = [
  { title: "Projektanalyse erstellen", desc: "Strukturierte Auswertung über alle Dokumente (file_search)" },
  { title: "Projektanalyse v2 erstellen", desc: "Volltext-Analyse — Dokumente komplett im Modell-Kontext" },
];

function FigureThumb({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen: () => void;
}) {
  const [url, setUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!citation.image_path) return;
    let cancelled = false;
    const supabase = createClient();
    supabase.storage
      .from("chunk-images")
      .createSignedUrl(citation.image_path, 600)
      .then(({ data }) => {
        if (cancelled) return;
        setUrl(data?.signedUrl ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [citation.image_path]);
  if (!url) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="block mt-3 rounded-[10px] overflow-hidden border border-border bg-bg-elevated cursor-pointer transition-[border-color,box-shadow] duration-150 hover:border-border-strong hover:shadow-[0_0_0_3px_color-mix(in_oklch,var(--accent)_18%,transparent)]"
    >
      <img
        src={url}
        alt={citation.figure_label ?? "Figure"}
        className="block max-w-md max-h-80 object-contain bg-[#1a1a1a]"
      />
      <div className="text-[11px] text-text-tertiary px-2 py-1.5 text-left border-t border-border">
        {citation.figure_label ?? "Figure"} · {citation.filename} p.{citation.page_start}
      </div>
    </button>
  );
}

function linkifyCitations(text: string, citations: Citation[]): string {
  return text.replace(/\[(\d+)\]/g, (m, n) => {
    const idx = parseInt(n, 10) - 1;
    if (idx < 0 || idx >= citations.length) return m;
    return `[${m}](#cite-${idx})`;
  });
}

export function Message({
  msg,
  streaming,
  onCiteClick,
}: {
  msg: Msg;
  streaming: boolean;
  onCiteClick?: (c: Citation) => void;
}) {
  const isUser = msg.role === "user";
  if (isUser) {
    return (
      <div className="group flex flex-col items-end">
        <div className="bg-bg-bubble text-text rounded-[18px] py-2.5 px-4 max-w-[70%] text-[14.5px] leading-[1.55] whitespace-pre-wrap break-words">
          {msg.content}
        </div>
      </div>
    );
  }
  const citations = msg.citations ?? [];
  const showLoadingDots = streaming && !msg.content;
  // Backend sends one citation per `ref` index parallel to the chunks the
  // model saw. Two transforms happen here in one pass so the prose, the
  // chip list, and click-to-cite stay in lockstep:
  //   1. Dedupe by chunk_id — the same chunk returned by two tool calls
  //      should produce one chip, with both prose refs pointing at it.
  //   2. Renumber sequentially per answer — original refs may have gaps
  //      (only some retrievals were cited) and may not start at 1.
  // Builds visibleCitations[i] paired with newRef = i+1, plus a map from
  // each old ref appearing in prose to its new ref.
  const { visibleCitations, renderedContent } = React.useMemo(() => {
    const oldToNew = new Map<number, number>();
    const chunkIdToNew = new Map<string, number>();
    const visible: Citation[] = [];
    for (const m of msg.content.matchAll(/\[(\d+)\]/g)) {
      const oldRef = parseInt(m[1], 10);
      if (oldToNew.has(oldRef)) continue;
      if (oldRef < 1 || oldRef > citations.length) continue;
      const c = citations[oldRef - 1];
      const existingNew = chunkIdToNew.get(c.chunk_id);
      if (existingNew != null) {
        oldToNew.set(oldRef, existingNew);
        continue;
      }
      visible.push(c);
      const newRef = visible.length;
      chunkIdToNew.set(c.chunk_id, newRef);
      oldToNew.set(oldRef, newRef);
    }
    const rewritten = msg.content.replace(/\[(\d+)\]/g, (full, n) => {
      const newN = oldToNew.get(parseInt(n, 10));
      return newN != null ? `[${newN}]` : full;
    });
    const linked = visible.length ? linkifyCitations(rewritten, visible) : rewritten;
    return { visibleCitations: visible, renderedContent: linked };
  }, [msg.content, citations]);
  // De-dup figure thumbs by image_path. Cap at 3 so a chunky retrieval
  // set doesn't blow up the message height. Only includes figures the
  // model actually cited.
  const figureCitations: Citation[] = [];
  const seenImage = new Set<string>();
  for (const c of visibleCitations) {
    if (!c.image_path || seenImage.has(c.image_path)) continue;
    seenImage.add(c.image_path);
    figureCitations.push(c);
    if (figureCitations.length >= 3) break;
  }
  return (
    <div className="group flex flex-col items-stretch">
      {showLoadingDots && (
        <div
          className="flex items-center gap-1.5 py-1"
          role="status"
          aria-label="Antwort wird vorbereitet"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:0ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:150ms]" />
          <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:300ms]" />
        </div>
      )}
      <div className={MD_PROSE}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children, ...rest }) => {
              const m = href?.match(/^#cite-(\d+)$/);
              if (m) {
                const idx = parseInt(m[1], 10);
                const c = citations[idx];
                if (c) {
                  return (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        onCiteClick?.(c);
                      }}
                      title={c.snippet}
                      className="font-mono tabular-nums text-[12px] text-accent hover:text-accent-hover underline underline-offset-2 align-baseline px-px"
                    >
                      {children}
                    </button>
                  );
                }
              }
              return (
                <a href={href} target="_blank" rel="noreferrer" {...rest}>
                  {children}
                </a>
              );
            },
          }}
        >
          {renderedContent}
        </ReactMarkdown>
      </div>
      {figureCitations.length > 0 && (
        <details className="mt-3 group/figs">
          <summary className="cursor-pointer text-[12px] text-text-tertiary hover:text-text-secondary list-none [&::-webkit-details-marker]:hidden inline-flex items-center gap-1.5 select-none">
            <span className="transition-transform duration-150 group-open/figs:rotate-90">›</span>
            Bilder anzeigen ({figureCitations.length})
          </summary>
          <div className="flex flex-wrap gap-3 mt-2">
            {figureCitations.map((c) => (
              <FigureThumb
                key={c.chunk_id}
                citation={c}
                onOpen={() => onCiteClick?.(c)}
              />
            ))}
          </div>
        </details>
      )}
      {visibleCitations.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {visibleCitations.map((c, i) => (
            <CitationChip
              key={`${c.chunk_id}-${i}`}
              citation={c}
              index={i + 1}
              onClick={() => onCiteClick?.(c)}
            />
          ))}
        </div>
      )}
      {!streaming && (
        <div className="flex gap-1 mt-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <button className="bg-transparent border border-transparent rounded-md px-2 py-1 text-text-tertiary text-[11px] inline-flex items-center gap-[5px] transition-[background-color,color,border-color] duration-150 hover:bg-bg-hover hover:text-text hover:border-border">
            <Icon.Copy /> Kopieren
          </button>
          <button className="bg-transparent border border-transparent rounded-md px-2 py-1 text-text-tertiary text-[11px] inline-flex items-center gap-[5px] transition-[background-color,color,border-color] duration-150 hover:bg-bg-hover hover:text-text hover:border-border">
            ↻ Neu generieren
          </button>
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
  noProjects = false,
}: {
  onSuggest: (title: string) => void;
  hasFiles?: boolean;
  projectName?: string;
  onAddFiles?: () => void;
  userName?: string;
  noProjects?: boolean;
}) {
  if (noProjects) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 gap-7">
        <div className="font-display text-[56px] font-extrabold tracking-[-0.04em] text-text">
          EAG <span className="text-accent">LLM</span>
        </div>
        <div className="font-display text-[28px] font-medium tracking-[-0.02em] text-text text-center">
          Hallo, {userName}. <span className="text-text-tertiary">Erstelle ein Projekt, um zu beginnen.</span>
        </div>
      </div>
    );
  }
  if (!hasFiles) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 gap-7">
        <div className="font-display text-[56px] font-extrabold tracking-[-0.04em] text-text">
          EAG <span className="text-accent">LLM</span>
        </div>
        <div className="font-display text-[28px] font-medium tracking-[-0.02em] text-text text-center">Hallo, {userName}.</div>
        <div className="text-sm text-text-tertiary -mt-1 mb-[22px] max-w-[420px] text-center leading-[1.5]">
          Lade Dokumente hoch, damit ich auf Basis deiner Projektdateien antworten kann.
        </div>
        <button
          className="inline-flex items-center gap-2 px-4 py-2.5 bg-accent text-[#1a0a05] rounded-[9px] text-[13px] font-semibold cursor-pointer transition-[background-color,transform] duration-150 hover:bg-accent-hover active:translate-y-px [&_svg]:w-4 [&_svg]:h-4"
          onClick={() => onAddFiles && onAddFiles()}
        >
          <Icon.Paperclip /> Dateien hinzufügen
        </button>
        <div className="mt-3.5 text-xs text-text-tertiary tracking-[0.01em]">Unterstützte Formate: PDF, Office, Bild, TXT, MD, CSV</div>
      </div>
    );
  }
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-12 gap-7">
      <div className="font-display text-[56px] font-extrabold tracking-[-0.04em] text-text">
        EAG <span className="text-accent">LLM</span>
      </div>
      <div className="font-display text-[28px] font-medium tracking-[-0.02em] text-text text-center">
        Hallo, {userName}. <span className="text-text-tertiary">Was kann ich für dich heraussuchen?</span>
      </div>
      <div className="grid grid-cols-[repeat(2,minmax(0,280px))] gap-2.5 w-full max-w-[600px]">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.title}
            className="bg-bg-elevated border border-border rounded-md py-3.5 px-4 text-left text-text flex flex-col gap-1 transition-[border-color,background-color,transform] duration-150 hover:border-border-strong hover:bg-bg-hover active:translate-y-px"
            onClick={() => onSuggest(s.title)}
          >
            <div className="text-[13px] font-medium text-text">{s.title}</div>
            <div className="text-xs text-text-tertiary">{s.desc}</div>
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

const DD_MENU =
  "absolute bottom-[calc(100%+4px)] z-[60] min-w-[200px] bg-bg-elevated border border-border " +
  "rounded-[8px] shadow-[0_8px_24px_rgba(0,0,0,.12),0_2px_6px_rgba(0,0,0,.06)] p-1 flex flex-col";

const MENU_ITEM =
  "flex items-center gap-2 px-2.5 py-[7px] rounded-[5px] bg-transparent border-none " +
  "text-[13px] text-text text-left cursor-pointer transition-[background-color] duration-100 " +
  "hover:bg-bg-hover [&_svg]:text-text-tertiary [&_svg]:flex-shrink-0 [&:hover_svg]:text-text-secondary";

const ICON_BTN =
  "w-7 h-7 inline-flex items-center justify-center bg-transparent border-none rounded-md " +
  "text-text-tertiary transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text";

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
    <div className="relative inline-flex" ref={ref}>
      {React.cloneElement(trigger, {
        onClick: (e: React.MouseEvent) => {
          e.stopPropagation();
          setOpen((v) => !v);
        },
      })}
      {open && (
        <div
          className={DD_MENU + " " + (align === "end" ? "right-0" : "left-0")}
          onClick={(e) => e.stopPropagation()}
        >
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
    <div className="composer-wrap px-6 pt-3 pb-[22px] flex-shrink-0">
      <form
        className="w-full max-w-[760px] mx-auto bg-bg-elevated border border-border rounded-[12px] transition-[border-color] duration-150 focus-within:border-border-strong"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-4 py-2.5 border-b border-border rounded-t-[11px]">
            {attachments.map((f) => (
              <span
                key={f.id}
                className="inline-flex items-center gap-1.5 h-6 pl-2 pr-1 bg-bg-input rounded-full text-xs text-text [&_svg]:text-text-tertiary [&_svg]:flex-shrink-0"
              >
                <Icon.File />
                <span className="max-w-[180px] overflow-hidden text-ellipsis whitespace-nowrap">{f.name}</span>
                <span className="text-[10px] text-text-tertiary font-mono">{f.size}</span>
                <button
                  type="button"
                  className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-transparent border-none text-text-tertiary ml-0.5 transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text"
                  onClick={() => removeFile(f.id)}
                  aria-label="Anhang entfernen"
                >
                  <Icon.X />
                </button>
              </span>
            ))}
          </div>
        )}

        <div className="pt-3 px-4 pb-2">
          <textarea
            ref={ref}
            rows={1}
            placeholder="Frag etwas…"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKey}
            className="w-full bg-transparent border-none [outline:none] text-text text-sm leading-[1.55] resize-none min-h-[24px] max-h-[208px] [font-family:inherit] placeholder:text-text-tertiary"
          />
        </div>

        {showSettings && (
          <div className="px-4 py-3 border-t border-border">
            <div className="flex justify-between items-baseline mb-2">
              <span className="text-xs font-medium text-text-secondary">Temperature</span>
              <span className="text-xs text-text-secondary font-mono tabular-nums">{temp.toFixed(2)}</span>
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
            <div className="flex justify-between mt-1 text-[10px] text-text-tertiary">
              <span>Präzise</span>
              <span>Kreativ</span>
            </div>
          </div>
        )}

        <div className="flex items-center gap-1 px-3 py-2 rounded-b-[11px]">
          <Dropdown
            trigger={
              <button type="button" className={ICON_BTN} title="Hinzufügen">
                <Icon.Plus />
              </button>
            }
          >
            {({ close }) => (
              <>
                <button type="button" className={MENU_ITEM} onClick={() => { addFile(); close(); }}>
                  <Icon.Paperclip /> Dateien hinzufügen
                </button>
                <button type="button" className={MENU_ITEM} onClick={close}>
                  <Icon.Sparkles /> Agent-Modus
                </button>
                <button type="button" className={MENU_ITEM} onClick={close}>
                  <Icon.SearchSm /> Deep Research
                </button>
              </>
            )}
          </Dropdown>

          <Dropdown
            trigger={
              <button
                type="button"
                className="h-7 inline-flex items-center gap-1 px-2 rounded-md bg-transparent border-none text-text-secondary text-xs font-medium whitespace-nowrap transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text [&_svg]:text-text-tertiary"
                title="Modell wählen"
              >
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
                    className={
                      MENU_ITEM +
                      " justify-between min-w-[240px]" +
                      (m.id === model.id ? " bg-bg-hover" : "")
                    }
                    onClick={() => { setModel(m); close(); }}
                  >
                    <span className="inline-flex items-baseline gap-1.5">
                      <span className="text-[13px] text-text">{m.name}</span>
                      <span className="text-[11px] text-text-tertiary">{m.provider}</span>
                    </span>
                    <span className="text-[10px] text-text-tertiary font-mono">{m.maxTokens}</span>
                  </button>
                ))}
              </>
            )}
          </Dropdown>

          <button
            type="button"
            className={ICON_BTN + (showSettings ? " text-text bg-bg-hover" : "")}
            onClick={() => setShowSettings((v) => !v)}
            title="Einstellungen"
          >
            <Icon.Sliders />
          </button>

          <div className="ml-auto flex items-center gap-1">
            {attachments.length > 0 && (
              <span className="text-[10px] text-text-tertiary font-mono tabular-nums mr-1">
                {attachments.length} {attachments.length === 1 ? "Datei" : "Dateien"}
              </span>
            )}
            <button type="button" className={ICON_BTN} title="Spracheingabe">
              <Icon.Mic />
            </button>
            {/* Always render the send button so the toolbar slot is the
                same DOM element regardless of state — no mount/unmount, no
                layout shift. Visibility + the pop-in are driven by Tailwind
                transitions on opacity and scale. */}
            {(() => {
              const visible = streaming || !!value.trim();
              const stopMode = streaming;
              return (
                <button
                  type={stopMode ? "button" : "submit"}
                  onClick={stopMode ? onStop : undefined}
                  title={stopMode ? "Generierung stoppen" : "Senden"}
                  aria-hidden={visible ? undefined : true}
                  tabIndex={visible ? 0 : -1}
                  className={
                    "cmp-send transition-[opacity,transform] duration-150 ease-out " +
                    (visible ? "opacity-100 scale-100" : "opacity-0 scale-75 pointer-events-none")
                  }
                >
                  {stopMode ? <Icon.Stop /> : <Icon.ArrowUp />}
                </button>
              );
            })()}
          </div>
        </div>
      </form>
      <div className="w-full max-w-[760px] mx-auto mt-2 text-center text-[11px] text-text-tertiary font-mono">
        EAG LLM greift auf deine indexierten Quellen zu. Wichtige Antworten bitte verifizieren.
      </div>
    </div>
  );
}
