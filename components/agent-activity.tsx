"use client";
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BotIcon,
  HammerIcon,
  CheckCircle2Icon,
  Loader2Icon,
  MessageSquareTextIcon,
  ChevronRightIcon,
  FileTextIcon,
  SparklesIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { TraceStep, TraceChunk } from "./fixtures";

// Friendly headline for the agent author, in Schweizer Deutsch ohne Umlaute
// to match the rest of the chat UI.
const AGENT_LABEL: Record<string, string> = {
  chat_orchestrator: "Orchestrator",
  rag_specialist: "RAG-Spezialist",
  document_retriever: "Dokumentensuche",
  web_researcher: "Web-Recherche",
  web_google_search: "Google-Suche",
  web_url_fetcher: "URL-Abruf",
  user: "Nutzer",
  unknown: "Unbekannt",
};

// Per-tool friendly verbs for paired call+response phases. The default
// "ruft <tool> auf" works for most tools; rag_specialist gets a more
// descriptive label since the user reads dozens of these per turn.
const TOOL_PHASE_LABEL: Record<string, string> = {
  rag_specialist: "fragt RAG-Spezialist",
  web_researcher: "fragt Web-Recherche",
  document_retriever: "Dokumentensuche",
  search_project_documents: "durchsucht Projekt-Dokumente",
  run_projektanalyse_v2: "startet Projektanalyse v2",
};

function safeJSONParse<T = unknown>(s: string | null | undefined): T | null {
  if (!s) return null;
  try {
    return JSON.parse(s) as T;
  } catch {
    return null;
  }
}

function prettyJSON(s: string | null | undefined): string {
  if (!s) return "";
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

// ---------------------------------------------------------------------------
// Phase grouping: pair tool_call with the matching tool_response by
// (author, name) so each sub-agent invocation renders as ONE collapsible
// instead of two separate ones (call card + response card). Calls and
// responses can interleave with other steps in parallel fan-out, so we
// pair the i-th call of a given (author, name) with the i-th response of
// the same key in arrival order.
// ---------------------------------------------------------------------------

type ToolPhase = {
  kind: "tool_phase";
  id: string;          // stable id (uses the call's id)
  author: string;
  name: string;
  call: TraceStep;
  response: TraceStep | null;
};

type ModelTextPhase = {
  kind: "model_text";
  id: string;
  author: string;
  step: TraceStep;
};

type Phase = ToolPhase | ModelTextPhase;

function buildPhases(steps: TraceStep[]): Phase[] {
  const phases: Phase[] = [];
  // Per-(author, name) queue of indices into `phases` for tool_calls that
  // haven't been matched to a tool_response yet.
  const pending = new Map<string, number[]>();
  for (const s of steps) {
    if (s.kind === "tool_call") {
      const key = `${s.author}::${s.name ?? ""}`;
      const idx = phases.length;
      phases.push({
        kind: "tool_phase",
        id: s.id,
        author: s.author,
        name: s.name ?? "",
        call: s,
        response: null,
      });
      const q = pending.get(key) ?? [];
      q.push(idx);
      pending.set(key, q);
    } else if (s.kind === "tool_response") {
      const key = `${s.author}::${s.name ?? ""}`;
      const q = pending.get(key);
      if (q && q.length > 0) {
        const idx = q.shift()!;
        const phase = phases[idx];
        if (phase.kind === "tool_phase") phase.response = s;
      } else {
        // Orphan response — render as its own phase so we don't drop data.
        phases.push({
          kind: "tool_phase",
          id: s.id,
          author: s.author,
          name: s.name ?? "",
          call: s, // reuse — minimal display fallback
          response: s,
        });
      }
    } else {
      phases.push({
        kind: "model_text",
        id: s.id,
        author: s.author,
        step: s,
      });
    }
  }
  return phases;
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

const MD_TRACE =
  "text-[13.5px] leading-[1.6] text-text-secondary " +
  "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 " +
  "[&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:pl-4 [&_ul]:list-disc " +
  "[&_ol]:my-1.5 [&_ol]:pl-4 [&_ol]:list-decimal " +
  "[&_li]:my-0.5 [&_strong]:font-semibold [&_em]:italic " +
  "[&_code]:font-mono [&_code]:text-[12.5px] [&_code]:bg-bg-input " +
  "[&_code]:border [&_code]:border-border [&_code]:px-1 [&_code]:py-px [&_code]:rounded-[3px]";

function ChunkRow({ idx, chunk }: { idx: number; chunk: TraceChunk | null }) {
  // Prefer Konfidenz from grounding_supports.confidence_scores (managed
  // retrieval). Legacy rows from raw vector retrieval expose `score` as a
  // cosine distance — convert to similarity for back-compat. Both render
  // higher = better so the reader gets one consistent direction.
  let scoreLabel: string | null = null;
  let scoreValue: number | null = null;
  let scoreTitle = "";
  if (typeof chunk?.confidence === "number") {
    scoreLabel = "Konfidenz";
    scoreValue = chunk.confidence;
    scoreTitle =
      "Grounding-Konfidenz aus Vertex RAG. 1.0 = sehr starke Bestaetigung, 0 = keine Belegung.";
  } else if (typeof chunk?.score === "number") {
    scoreLabel = "Aehnlichkeit";
    scoreValue = Math.max(0, 1 - chunk.score);
    scoreTitle =
      "Aehnlichkeit zur Anfrage (1 - Cosine-Distanz, Legacy-Pfad). 1.0 = identisch, 0 = unverwandt.";
  }
  // Prefer the full chunk text; fall back to the 200-char snippet for
  // citations created before the `text` field landed.
  const body = chunk?.text || chunk?.snippet || "";
  return (
    <div className="border border-border rounded-md bg-bg-base p-2.5">
      <div className="flex items-center gap-2 text-[11.5px] mb-1.5 flex-wrap">
        <Badge variant="secondary" className="font-mono text-[10px] py-0 px-1.5">
          [{idx}]
        </Badge>
        <FileTextIcon className="size-3 text-text-tertiary" />
        <span className="font-medium text-text truncate">
          {chunk?.filename ?? "Unbekannte Quelle"}
        </span>
        {chunk?.page_start != null && (
          <span className="text-text-tertiary">
            S. {chunk.page_start}
            {chunk.page_end && chunk.page_end !== chunk.page_start
              ? `–${chunk.page_end}`
              : ""}
          </span>
        )}
        {scoreValue != null && (
          <span
            className="ml-auto font-mono text-[10.5px] text-text-tertiary"
            title={scoreTitle}
          >
            {scoreLabel} {scoreValue.toFixed(3)}
          </span>
        )}
      </div>
      {body && (
        <div className="text-[12px] leading-[1.55] text-text-secondary whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
          {body}
        </div>
      )}
      {!chunk && (
        <div className="text-[12px] italic text-text-tertiary">
          Chunk-Daten noch nicht verfuegbar.
        </div>
      )}
    </div>
  );
}

function ChunksSubPanel({
  citedIdxs,
  chunksByIdx,
}: {
  citedIdxs: number[] | null | undefined;
  chunksByIdx: Record<number, TraceChunk> | null | undefined;
}) {
  const [open, setOpen] = React.useState(false);
  if (!citedIdxs || citedIdxs.length === 0) return null;
  return (
    <div className="border border-border rounded-md overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-bg-hover transition-colors"
      >
        <ChevronRightIcon
          className={
            "size-3 text-text-tertiary transition-transform duration-150 " +
            (open ? "rotate-90" : "")
          }
        />
        <FileTextIcon className="size-3 text-text-tertiary" />
        <span className="text-[12px] font-medium text-text">
          Abgerufene Chunks
        </span>
        <Badge variant="secondary" className="font-mono text-[10px] py-0 px-1.5">
          {citedIdxs.length}
        </Badge>
      </button>
      {open && (
        <div className="border-t border-border bg-bg-elevated p-2 space-y-2">
          {citedIdxs.map((idx) => (
            <ChunkRow
              key={idx}
              idx={idx}
              chunk={(chunksByIdx ?? {})[idx] ?? null}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolPhaseRow({
  phase,
  chunksByIdx,
  streaming,
}: {
  phase: ToolPhase;
  chunksByIdx: Record<number, TraceChunk> | null | undefined;
  streaming: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const author = AGENT_LABEL[phase.author] ?? phase.author;
  const verb = TOOL_PHASE_LABEL[phase.name] ?? `ruft ${phase.name} auf`;

  // Args: surface the `request` field for rag_specialist; otherwise show
  // the JSON object's first non-empty string field, falling back to pretty
  // JSON. The pretty JSON is also kept in `argsJSON` for the body.
  const argsObj = safeJSONParse<Record<string, unknown>>(phase.call.args);
  const requestPreview =
    typeof argsObj?.request === "string"
      ? (argsObj.request as string)
      : typeof argsObj?.query === "string"
        ? (argsObj.query as string)
        : null;

  const responseObj = safeJSONParse<Record<string, unknown>>(phase.response?.response);
  const resultText =
    typeof responseObj?.result === "string"
      ? (responseObj.result as string)
      : null;

  const isPending = !phase.response;
  const showLoader = isPending && streaming;

  return (
    <div className="border border-border rounded-md overflow-hidden bg-bg-base">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-2 px-2.5 py-2 text-left hover:bg-bg-hover transition-colors"
      >
        <ChevronRightIcon
          className={
            "size-3.5 mt-0.5 text-text-tertiary transition-transform duration-150 shrink-0 " +
            (open ? "rotate-90" : "")
          }
        />
        {showLoader ? (
          <Loader2Icon className="size-3.5 mt-0.5 text-accent animate-spin shrink-0" />
        ) : isPending ? (
          <HammerIcon className="size-3.5 mt-0.5 text-text-tertiary shrink-0" />
        ) : (
          <CheckCircle2Icon className="size-3.5 mt-0.5 text-accent shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-[12.5px]">
            <span className="font-medium text-text">{author}</span>
            <span className="text-text-tertiary">·</span>
            <span className="text-text-secondary">{verb}</span>
            {isPending && !showLoader && (
              <Badge variant="secondary" className="font-mono text-[10px] py-0 px-1.5">
                wartet
              </Badge>
            )}
          </div>
          {requestPreview && (
            <div className="mt-0.5 text-[12.5px] text-text-secondary line-clamp-2">
              {requestPreview}
            </div>
          )}
        </div>
      </button>
      {open && (
        <div className="border-t border-border bg-bg-elevated px-3 py-2.5 space-y-3">
          {requestPreview ? (
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1">
                Anfrage
              </div>
              <div className="text-[13px] leading-[1.5] text-text whitespace-pre-wrap">
                {requestPreview}
              </div>
            </div>
          ) : phase.call.args ? (
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1">
                Argumente
              </div>
              <pre className="bg-bg-input border border-border rounded-md p-2.5 text-[11.5px] leading-[1.5] font-mono text-text-secondary overflow-x-auto whitespace-pre-wrap break-words max-h-72">
                {prettyJSON(phase.call.args)}
              </pre>
            </div>
          ) : null}

          {showLoader && (
            <div className="flex items-center gap-2 text-[12.5px] text-text-tertiary italic">
              <Loader2Icon className="size-3.5 animate-spin" />
              wird geladen…
            </div>
          )}

          {resultText && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1">
                Antwort
              </div>
              <div className={MD_TRACE}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {resultText}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {!resultText && phase.response?.response && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1">
                Antwort
              </div>
              <pre className="bg-bg-input border border-border rounded-md p-2.5 text-[11.5px] leading-[1.5] font-mono text-text-secondary overflow-x-auto whitespace-pre-wrap break-words max-h-72">
                {prettyJSON(phase.response.response)}
              </pre>
            </div>
          )}

          <ChunksSubPanel
            citedIdxs={phase.response?.cited_idxs ?? null}
            chunksByIdx={chunksByIdx}
          />
        </div>
      )}
    </div>
  );
}

function ModelTextRow({ phase }: { phase: ModelTextPhase }) {
  const [open, setOpen] = React.useState(false);
  const author = AGENT_LABEL[phase.author] ?? phase.author;
  const text = phase.step.text ?? "";
  // Orchestrator model_text events are user-facing: the orchestrator is
  // synthesising the final answer (streamed via `delta` frames in parallel).
  // "denkt nach" misrepresents this — call it "verfasst Antwort" so the
  // step accurately reflects what the user is reading on the right side.
  const verb =
    phase.author === "chat_orchestrator"
      ? "verfasst Antwort"
      : "Zwischenueberlegung";
  return (
    <div className="border border-border rounded-md overflow-hidden bg-bg-base">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-2 px-2.5 py-2 text-left hover:bg-bg-hover transition-colors"
      >
        <ChevronRightIcon
          className={
            "size-3.5 mt-0.5 text-text-tertiary transition-transform duration-150 shrink-0 " +
            (open ? "rotate-90" : "")
          }
        />
        {phase.author === "chat_orchestrator" ? (
          <SparklesIcon className="size-3.5 mt-0.5 text-text-tertiary shrink-0" />
        ) : (
          <MessageSquareTextIcon className="size-3.5 mt-0.5 text-text-tertiary shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-[12.5px]">
            <span className="font-medium text-text">{author}</span>
            <span className="text-text-tertiary">·</span>
            <span className="text-text-secondary">{verb}</span>
          </div>
          {text && (
            <div className="mt-0.5 text-[12.5px] text-text-secondary line-clamp-2">
              {text}
            </div>
          )}
        </div>
      </button>
      {open && text && (
        <div className="border-t border-border bg-bg-elevated px-3 py-2.5">
          <div className={MD_TRACE}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

export function AgentActivity({
  steps,
  chunksByIdx,
  streaming,
}: {
  steps: TraceStep[];
  chunksByIdx?: Record<number, TraceChunk> | null;
  streaming: boolean;
}) {
  const [open, setOpen] = React.useState(streaming);
  // Auto-expand on first stream, leave alone after — once the run is done,
  // respect the user's collapsed state.
  React.useEffect(() => {
    if (streaming) setOpen(true);
  }, [streaming]);

  const phases = React.useMemo(() => buildPhases(steps), [steps]);

  if (!steps.length) return null;
  const stepCount = steps.length;
  const lastStep = steps[steps.length - 1];
  const lastAuthor = AGENT_LABEL[lastStep.author] ?? lastStep.author;

  return (
    <div className="mb-3 rounded-[10px] border border-border bg-bg-elevated overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-bg-hover"
      >
        <ChevronRightIcon
          className={
            "size-3.5 text-text-tertiary transition-transform duration-150 " +
            (open ? "rotate-90" : "")
          }
        />
        {streaming ? (
          <Loader2Icon className="size-3.5 text-accent animate-spin" />
        ) : (
          <BotIcon className="size-3.5 text-text-tertiary" />
        )}
        <span className="text-[12.5px] font-medium text-text">Agent-Aktivitaet</span>
        <Badge variant="secondary" className="font-mono text-[10px] py-0 px-1.5">
          {stepCount} {stepCount === 1 ? "Schritt" : "Schritte"}
        </Badge>
        {streaming && (
          <span className="text-[11px] text-text-tertiary">
            {lastAuthor} laeuft...
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-border bg-bg-base p-2 space-y-1.5">
          {phases.map((phase) =>
            phase.kind === "tool_phase" ? (
              <ToolPhaseRow
                key={phase.id}
                phase={phase}
                chunksByIdx={chunksByIdx}
                streaming={streaming}
              />
            ) : (
              <ModelTextRow key={phase.id} phase={phase} />
            )
          )}
        </div>
      )}
    </div>
  );
}
