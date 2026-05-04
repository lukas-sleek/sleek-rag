"use client";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "./icons";
import type { Citation, Message as Msg } from "./fixtures";
import { CitationChip } from "./citation-chip";
import { CitationHover } from "./citation-hover";
import { AgentActivity } from "./agent-activity";
import { api } from "@/lib/api";

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
  { title: "Projektanalyse erstellen", desc: "Beantwortet alle Fragen aus deiner Vorlage parallel über die Projektdokumente" },
];

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
    // Pattern A (Vertex RAG grounding) typically returns inline parenthetical
    // citations from the model — "(file.pdf, Seite 14)" — instead of [N]
    // markers. When the prose has no [N] hits we still want chips visible,
    // since the backend told us which chunks the model grounded on.
    // Dedupe the fallback list by chunk_id so identical chunks pulled by
    // multiple sub-queries don't render twice.
    if (visible.length === 0 && citations.length > 0) {
      const seenIds = new Set<string>();
      for (const c of citations) {
        if (seenIds.has(c.chunk_id)) continue;
        seenIds.add(c.chunk_id);
        visible.push(c);
      }
    }
    const linked = oldToNew.size ? linkifyCitations(rewritten, visible) : rewritten;
    return { visibleCitations: visible, renderedContent: linked };
  }, [msg.content, citations]);
  const contentRef = React.useRef<HTMLDivElement>(null);
  const [copied, setCopied] = React.useState(false);

  const stripCitations = (root: HTMLElement) => {
    // Citation links/buttons rendered by ReactMarkdown for "[N]" markers.
    root.querySelectorAll('a[href^="#cite-"], button[title]').forEach((el) => {
      if (el.tagName === "BUTTON" && !/^\[\d+\]$/.test(el.textContent ?? "")) return;
      el.remove();
    });
    // Any stray "[N]" text that survived (shouldn't, but safe).
    root.innerHTML = root.innerHTML.replace(/\[\d+\]/g, "");
  };

  const handleCopy = async () => {
    if (!contentRef.current) return;
    const clone = contentRef.current.cloneNode(true) as HTMLElement;
    stripCitations(clone);
    const text = (clone.innerText || "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const handleOpenInWord = () => {
    if (!contentRef.current) return;
    const clone = contentRef.current.cloneNode(true) as HTMLElement;
    stripCitations(clone);
    const body = clone.innerHTML;
    const html =
      '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
      'xmlns:w="urn:schemas-microsoft-com:office:word" ' +
      'xmlns="http://www.w3.org/TR/REC-html40">' +
      '<head><meta charset="utf-8"><title>EAG LLM Antwort</title>' +
      '<style>body{font-family:Calibri,Arial,sans-serif;font-size:11pt;line-height:1.45;}' +
      'h1{font-size:18pt;}h2{font-size:14pt;}h3{font-size:12pt;}' +
      'table{border-collapse:collapse;}th,td{border:1px solid #999;padding:4px 6px;}' +
      'code{font-family:Consolas,monospace;background:#f3f3f3;padding:1px 3px;}' +
      'pre{font-family:Consolas,monospace;background:#f3f3f3;padding:8px;}</style>' +
      '</head><body>' + body + '</body></html>';
    const blob = new Blob(["﻿", html], { type: "application/msword" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `eag-llm-antwort-${new Date().toISOString().slice(0, 10)}.doc`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div className="group flex flex-col items-stretch">
      {msg.traces && msg.traces.length > 0 && (
        <AgentActivity steps={msg.traces} streaming={streaming} />
      )}
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
      <div className={MD_PROSE} ref={contentRef}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children, ...rest }) => {
              const m = href?.match(/^#cite-(\d+)$/);
              if (m) {
                const idx = parseInt(m[1], 10);
                // `linkifyCitations` writes #cite-{newRef-1} where newRef
                // indexes into the deduped+renumbered visibleCitations
                // array. Earlier this dereferenced `citations` (the raw
                // backend list), which mismatched whenever dedupe collapsed
                // earlier markers — chip [9] would open the wrong file.
                const c = visibleCitations[idx];
                if (c) {
                  return (
                    <CitationHover
                      citation={c}
                      onClick={() => onCiteClick?.(c)}
                    >
                      {children}
                    </CitationHover>
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
      {!streaming && msg.content && (
        <div className="flex gap-1 mt-2.5 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <button
            type="button"
            onClick={handleCopy}
            className="bg-transparent border border-transparent rounded-md px-2 py-1 text-text-tertiary text-[11px] inline-flex items-center gap-[5px] transition-[background-color,color,border-color] duration-150 hover:bg-bg-hover hover:text-text hover:border-border"
          >
            <Icon.Copy /> {copied ? "Kopiert" : "Kopieren"}
          </button>
          <button
            type="button"
            onClick={handleOpenInWord}
            className="bg-transparent border border-transparent rounded-md px-2 py-1 text-text-tertiary text-[11px] inline-flex items-center gap-[5px] transition-[background-color,color,border-color] duration-150 hover:bg-bg-hover hover:text-text hover:border-border"
          >
            <Icon.FileText /> In Word öffnen
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
      <div className="flex flex-wrap justify-center gap-2.5 w-full max-w-[600px]">
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
  filesProcessing = false,
}: {
  onSend: (text: string) => void;
  streaming: boolean;
  onStop: () => void;
  filesProcessing?: boolean;
}) {
  const [value, setValue] = React.useState("");
  const [temp, setTemp] = React.useState(0.7);
  const [showSettings, setShowSettings] = React.useState(false);
  const [attachments, setAttachments] = React.useState<Attachment[]>([]);
  const [listening, setListening] = React.useState(false);
  const [transcribing, setTranscribing] = React.useState(false);
  const [micDevices, setMicDevices] = React.useState<MediaDeviceInfo[]>([]);
  const [micDeviceId, setMicDeviceId] = React.useState<string>(() => {
    if (typeof window === "undefined") return "";
    return window.localStorage.getItem("micDeviceId") || "";
  });
  const ref = React.useRef<HTMLTextAreaElement>(null);
  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const streamRef = React.useRef<MediaStream | null>(null);
  const audioCtxRef = React.useRef<AudioContext | null>(null);
  const analyserRef = React.useRef<AnalyserNode | null>(null);
  const rafRef = React.useRef<number | null>(null);
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);

  const stopVisualizer = () => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    try { audioCtxRef.current?.close(); } catch {}
    audioCtxRef.current = null;
    analyserRef.current = null;
  };

  const startVisualizer = (stream: MediaStream) => {
    const Ctx =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    const ctx: AudioContext = new Ctx();
    const src = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.6;
    src.connect(analyser);
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;

    const buf = new Uint8Array(analyser.fftSize);
    const draw = () => {
      const canvas = canvasRef.current;
      const a = analyserRef.current;
      if (!canvas || !a) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      const cssW = canvas.clientWidth;
      const cssH = canvas.clientHeight;
      if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
        canvas.width = cssW * dpr;
        canvas.height = cssH * dpr;
      }
      const c = canvas.getContext("2d");
      if (!c) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, cssW, cssH);

      a.getByteTimeDomainData(buf);
      // Apple-style: thin rounded lines, more pillars, generous gap.
      // At silence the min height equals the stroke width so each bar
      // collapses to a perfect dot.
      const stroke = 2;
      const gap = 5;
      const bars = Math.max(12, Math.floor((cssW + gap) / (stroke + gap)));
      const slice = Math.floor(buf.length / bars);
      const totalW = bars * stroke + (bars - 1) * gap;
      const startX = (cssW - totalW) / 2;
      const mid = cssH / 2;
      const accent =
        getComputedStyle(document.documentElement).getPropertyValue("--accent") ||
        "#ff8a3d";
      c.strokeStyle = accent.trim();
      c.lineWidth = stroke;
      c.lineCap = "round";
      for (let i = 0; i < bars; i++) {
        let sumSq = 0;
        for (let j = 0; j < slice; j++) {
          const v = (buf[i * slice + j] - 128) / 128;
          sumSq += v * v;
        }
        const rms = Math.sqrt(sumSq / slice);
        const h = Math.max(0, Math.min(cssH - stroke, rms * cssH * 3.2));
        const x = startX + i * (stroke + gap) + stroke / 2;
        c.beginPath();
        c.moveTo(x, mid - h / 2);
        c.lineTo(x, mid + h / 2);
        c.stroke();
      }
      rafRef.current = requestAnimationFrame(draw);
    };
    rafRef.current = requestAnimationFrame(draw);
  };

  const refreshMicDevices = React.useCallback(async () => {
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) return;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setMicDevices(all.filter((d) => d.kind === "audioinput"));
    } catch {}
  }, []);

  React.useEffect(() => {
    refreshMicDevices();
    if (typeof navigator === "undefined" || !navigator.mediaDevices) return;
    const onChange = () => refreshMicDevices();
    navigator.mediaDevices.addEventListener?.("devicechange", onChange);
    return () => navigator.mediaDevices.removeEventListener?.("devicechange", onChange);
  }, [refreshMicDevices]);

  const selectMic = (id: string) => {
    setMicDeviceId(id);
    if (typeof window !== "undefined") window.localStorage.setItem("micDeviceId", id);
  };

  const stopMic = () => {
    try { recorderRef.current?.stop(); } catch {}
  };

  const toggleMic = async () => {
    if (listening) { stopMic(); return; }
    if (transcribing) return;
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      alert("Mikrofon wird in diesem Browser nicht unterstützt.");
      return;
    }
    let stream: MediaStream;
    try {
      const audio: MediaTrackConstraints | boolean = micDeviceId
        ? { deviceId: { exact: micDeviceId } }
        : true;
      stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch {
      alert("Mikrofon-Zugriff wurde abgelehnt.");
      return;
    }
    // After the first permission grant, enumerateDevices() will populate
    // device labels. Refresh now so the dropdown shows real names.
    refreshMicDevices();
    streamRef.current = stream;
    chunksRef.current = [];
    startVisualizer(stream);
    const mime =
      ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find(
        (t) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(t)
      ) || "";
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
    rec.onstop = async () => {
      stopVisualizer();
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setListening(false);
      const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
      chunksRef.current = [];
      if (blob.size === 0) return;
      setTranscribing(true);
      try {
        const fd = new FormData();
        fd.append("audio", blob, `voice.${(rec.mimeType.split("/")[1] || "webm").split(";")[0]}`);
        const res = await api("/api/transcribe", { method: "POST", body: fd });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const { text } = (await res.json()) as { text: string };
        if (text) {
          setValue((prev) => (prev ? prev.trimEnd() + " " : "") + text);
        }
      } catch (err) {
        alert("Transkription fehlgeschlagen.");
      } finally {
        setTranscribing(false);
      }
    };
    recorderRef.current = rec;
    setListening(true);
    rec.start();
    // 60s hard cap — STT v2 sync recognize tops out around there.
    setTimeout(() => { if (rec.state === "recording") stopMic(); }, 58_000);
  };

  React.useEffect(() => {
    return () => {
      try { recorderRef.current?.stop(); } catch {}
      streamRef.current?.getTracks().forEach((t) => t.stop());
      stopVisualizer();
    };
  }, []);

  React.useEffect(() => {
    if (!ref.current) return;
    ref.current.style.height = "auto";
    ref.current.style.height = Math.min(ref.current.scrollHeight, 208) + "px";
  }, [value]);

  const submit = () => {
    const v = value.trim();
    if (!v || streaming || filesProcessing) return;
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

        <div className="pt-3 px-4 pb-2 relative">
          <textarea
            ref={ref}
            rows={1}
            placeholder={
              transcribing
                ? "Transkribiere…"
                : listening
                ? ""
                : filesProcessing
                ? "Dateien werden noch verarbeitet…"
                : "Frag etwas…"
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKey}
            disabled={listening || transcribing || filesProcessing}
            className={
              "w-full bg-transparent border-none [outline:none] text-text text-sm leading-[1.55] resize-none min-h-[24px] max-h-[208px] [font-family:inherit] placeholder:text-text-tertiary disabled:cursor-not-allowed " +
              (listening ? "opacity-0 pointer-events-none" : "")
            }
          />
          {listening && (
            <div className="absolute inset-0 px-4 pt-3 pb-2 flex items-center gap-3 pointer-events-none">
              <span
                className="inline-flex items-center gap-1.5 text-[11px] font-medium text-accent shrink-0"
                aria-live="polite"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                Aufnahme läuft
              </span>
              <canvas
                ref={canvasRef}
                className="flex-1 h-6"
                aria-hidden="true"
              />
            </div>
          )}
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
          <button
            type="button"
            disabled={streaming || filesProcessing}
            onClick={() => onSend("Projektanalyse erstellen")}
            title={filesProcessing ? "Dateien werden noch verarbeitet…" : "Beantwortet alle Fragen aus deiner Vorlage parallel"}
            className="h-7 inline-flex items-center gap-1.5 px-2.5 rounded-md bg-transparent border border-transparent text-text-secondary text-xs font-medium whitespace-nowrap transition-[background-color,color,border-color,opacity] duration-150 hover:bg-bg-hover hover:text-text hover:border-border disabled:opacity-45 disabled:cursor-not-allowed [&_svg]:text-text-tertiary"
          >
            <Icon.Sparkles /> Projektanalyse erstellen
          </button>

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
            <div className="inline-flex items-center">
              <button
                type="button"
                onClick={toggleMic}
                disabled={transcribing || listening}
                className={
                  ICON_BTN +
                  " !w-7 !pr-1" +
                  (listening ? " text-accent" : "") +
                  (transcribing ? " opacity-50 cursor-wait animate-pulse" : "")
                }
                title={
                  transcribing
                    ? "Transkribiere…"
                    : listening
                    ? "Aufnahme läuft – mit Stop-Button beenden"
                    : "Spracheingabe starten"
                }
                aria-pressed={listening}
              >
                <Icon.Mic />
              </button>
              <Dropdown
                align="end"
                trigger={
                  <button
                    type="button"
                    onClick={() => { if (micDevices.length === 0) refreshMicDevices(); }}
                    disabled={listening || transcribing}
                    className="h-7 w-4 inline-flex items-center justify-center bg-transparent border-none rounded-md text-text-tertiary transition-[background-color,color] duration-150 hover:bg-bg-hover hover:text-text disabled:opacity-50"
                    title="Mikrofon wählen"
                  >
                    <Icon.ChevronDownSm />
                  </button>
                }
              >
                {({ close }) => (
                  <>
                    <div className="px-2.5 py-1.5 text-[10px] font-medium uppercase tracking-wide text-text-tertiary">
                      Mikrofon
                    </div>
                    {micDevices.length === 0 ? (
                      <div className="px-2.5 py-2 text-[12px] text-text-tertiary min-w-[240px]">
                        Keine Eingabegeräte gefunden. Mikrofon-Zugriff einmal erlauben, dann erneut öffnen.
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          className={
                            MENU_ITEM +
                            " min-w-[260px]" +
                            (micDeviceId === "" ? " bg-bg-hover" : "")
                          }
                          onClick={() => { selectMic(""); close(); }}
                        >
                          <span className="text-[13px]">System-Standard</span>
                        </button>
                        {micDevices.map((d, i) => (
                          <button
                            key={d.deviceId || `dev-${i}`}
                            type="button"
                            className={
                              MENU_ITEM +
                              " min-w-[260px]" +
                              (d.deviceId === micDeviceId ? " bg-bg-hover" : "")
                            }
                            onClick={() => { selectMic(d.deviceId); close(); }}
                          >
                            <span className="text-[13px] truncate max-w-[280px]">
                              {d.label || `Mikrofon ${i + 1}`}
                            </span>
                          </button>
                        ))}
                      </>
                    )}
                  </>
                )}
              </Dropdown>
            </div>
            {/* Always render the send button so the toolbar slot is the
                same DOM element regardless of state — no mount/unmount, no
                layout shift. Visibility + the pop-in are driven by Tailwind
                transitions on opacity and scale. */}
            {(() => {
              // Single button slot, four modes:
              //   listening    → stop icon, click stops mic recording
              //   streaming    → stop icon, click stops generation
              //   transcribing → hidden (waiting for STT roundtrip)
              //   has-value    → send icon, submits form
              const stopMode = streaming || listening;
              const visible = !transcribing && (stopMode || !!value.trim());
              const onClick = listening
                ? stopMic
                : streaming
                ? onStop
                : undefined;
              const title = listening
                ? "Aufnahme stoppen"
                : streaming
                ? "Generierung stoppen"
                : "Senden";
              return (
                <button
                  type={stopMode ? "button" : "submit"}
                  onClick={onClick}
                  title={title}
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
