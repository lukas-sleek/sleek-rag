"use client";
import * as React from "react";
import {
  BotIcon,
  BrainIcon,
  HammerIcon,
  CheckCircle2Icon,
  FileTextIcon,
  Loader2Icon,
  MessageSquareTextIcon,
  ChevronRightIcon,
} from "lucide-react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import type { RetrievalChunk, TraceStep } from "./fixtures";

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

const KIND_LABEL: Record<TraceStep["kind"], string> = {
  tool_call: "ruft Tool auf",
  tool_response: "Tool-Ergebnis",
  model_text: "antwortet",
  model_thought: "denkt laut",
};

function KindIcon({ kind }: { kind: TraceStep["kind"] }) {
  if (kind === "tool_call") return <HammerIcon className="size-3.5" />;
  if (kind === "tool_response") return <CheckCircle2Icon className="size-3.5" />;
  if (kind === "model_thought")
    return <BrainIcon className="size-3.5 text-amber-300" />;
  return <MessageSquareTextIcon className="size-3.5" />;
}

function prettyJSON(s: string | null | undefined): string {
  if (!s) return "";
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

function StepHeader({ step }: { step: TraceStep }) {
  const author = AGENT_LABEL[step.author] ?? step.author;
  const detail =
    step.kind === "tool_call" || step.kind === "tool_response"
      ? step.name ?? KIND_LABEL[step.kind]
      : KIND_LABEL[step.kind];
  // Tool-call rows in the activity panel can flip in place as the call
  // resolves: a frame with kind=tool_call gets upserted by a frame with
  // kind=tool_response sharing the same id (function_call.id from Gemini,
  // or `dispatch-<idx>` for batched questions). Render an inline status
  // pill so the user sees at a glance which calls are still running.
  let dispatchPhase: "start" | "done" | "error" | null = null;
  if (step.kind === "tool_call") dispatchPhase = "start";
  else if (step.kind === "tool_response" && step.status === "error")
    dispatchPhase = "error";
  else if (step.kind === "tool_response") dispatchPhase = "done";
  return (
    <div className="flex items-center gap-2 text-left text-[12.5px] w-full">
      <KindIcon kind={step.kind} />
      <span className="font-medium text-text">{author}</span>
      <span className="text-text-tertiary">·</span>
      <span className="text-text-secondary">{detail}</span>
      {dispatchPhase === "start" && (
        <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-amber-300">
          <Loader2Icon className="size-3 animate-spin" />
          laeuft
        </span>
      )}
      {dispatchPhase === "done" && (
        <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-emerald-300">
          <CheckCircle2Icon className="size-3" />
          fertig
        </span>
      )}
      {dispatchPhase === "error" && (
        <span className="ml-auto text-[10.5px] uppercase tracking-wider text-rose-300">
          fehler
        </span>
      )}
    </div>
  );
}

// Vertex `RagContext.score` for the default RagManagedVertexVectorSearch
// backend is a vector-DISTANCE (lower = more relevant), not a similarity.
// The presence of `vector_distance_threshold` in the retrieval config —
// which "returns contexts smaller than the threshold" — is the giveaway.
// So our thresholds invert the usual similarity-style colouring:
//   <= 0.30  → green (top-of-corpus match for normalised cosine distance)
//   <= 0.50  → amber
//   >  0.50  → rose
function distanceBadgeProps(distance: number): {
  variant: "default" | "secondary" | "outline";
  className: string;
} {
  if (distance <= 0.30) {
    return {
      variant: "default",
      className: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    };
  }
  if (distance <= 0.50) {
    return {
      variant: "secondary",
      className: "bg-amber-500/15 text-amber-300 border border-amber-500/30",
    };
  }
  return {
    variant: "outline",
    className: "bg-rose-500/10 text-rose-300 border border-rose-500/30",
  };
}

function ChunkRow({ chunk }: { chunk: RetrievalChunk }) {
  const scoreText =
    chunk.score === null || chunk.score === undefined
      ? "—"
      : chunk.score.toFixed(3);
  const badgeProps =
    chunk.score === null || chunk.score === undefined
      ? {
          variant: "outline" as const,
          className: "border border-border text-text-tertiary",
        }
      : distanceBadgeProps(chunk.score);
  return (
    <div className="rounded-md border border-border bg-bg-input p-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <FileTextIcon className="size-3 text-text-tertiary shrink-0" />
        <span className="text-[11px] font-mono text-text-tertiary">[{chunk.idx}]</span>
        <span className="text-[12px] font-medium text-text truncate flex-1">
          {chunk.filename}
        </span>
        <Badge
          variant={badgeProps.variant}
          title="Vektor-Distanz (niedriger = relevanter)"
          className={"font-mono text-[10px] py-0 px-1.5 " + badgeProps.className}
        >
          {scoreText}
        </Badge>
      </div>
      <p className="text-[11.5px] leading-[1.45] text-text-secondary whitespace-pre-wrap break-words">
        {chunk.snippet}
      </p>
    </div>
  );
}

function StepBody({ step }: { step: TraceStep }) {
  // search_project_documents responses get a structured chunk list with
  // confidence badges instead of the truncated-JSON preview.
  if (step.chunks && step.chunks.length > 0) {
    return (
      <div className="px-3 pb-3 space-y-2">
        <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1 flex items-center gap-2">
          <span>Treffer</span>
          <span className="text-text-tertiary">·</span>
          <span>{step.chunks.length}</span>
          <span className="text-text-tertiary normal-case tracking-normal italic ml-auto">
            Distanz ↓ besser
          </span>
          {step.status && step.status !== "ok" && (
            <span className="text-text-tertiary italic">({step.status})</span>
          )}
        </div>
        <div className="space-y-1.5">
          {step.chunks.map((c) => (
            <ChunkRow key={`${step.id}-${c.idx}`} chunk={c} />
          ))}
        </div>
      </div>
    );
  }
  if (step.chunks && step.chunks.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary px-3 pb-3 italic">
        Keine Treffer{step.status ? ` (${step.status})` : ""}.
      </div>
    );
  }

  const blocks: Array<{ label: string; body: string; thought?: boolean }> = [];
  if (step.args) blocks.push({ label: "Argumente", body: prettyJSON(step.args) });
  if (step.response) blocks.push({ label: "Antwort", body: prettyJSON(step.response) });
  if (step.text) {
    const isThought = step.kind === "model_thought";
    blocks.push({
      label: isThought ? "Gedanke" : "Inhalt",
      body: step.text,
      thought: isThought,
    });
  }
  if (blocks.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary px-3 pb-3 italic">
        keine Detail-Daten
      </div>
    );
  }
  return (
    <div className="px-3 pb-3 space-y-2">
      {blocks.map((b) => (
        <div key={b.label}>
          <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1 flex items-center gap-1.5">
            {b.thought && <BrainIcon className="size-3 text-amber-300" />}
            {b.label}
          </div>
          <pre
            className={
              b.thought
                ? "bg-amber-500/5 border border-amber-500/20 rounded-md p-2.5 text-[11.5px] leading-[1.55] text-amber-100/80 italic overflow-x-auto whitespace-pre-wrap break-words max-h-72"
                : "bg-bg-input border border-border rounded-md p-2.5 text-[11.5px] leading-[1.5] font-mono text-text-secondary overflow-x-auto whitespace-pre-wrap break-words max-h-72"
            }
          >
            {b.body}
          </pre>
        </div>
      ))}
    </div>
  );
}

export function AgentActivity({
  steps,
  streaming,
}: {
  steps: TraceStep[];
  streaming: boolean;
}) {
  const [open, setOpen] = React.useState(streaming);
  // Auto-expand on first stream, leave alone after — once the run is done,
  // respect the user's collapsed state.
  React.useEffect(() => {
    if (streaming) setOpen(true);
  }, [streaming]);

  if (!steps.length) return null;
  const stepCount = steps.length;
  const lastAuthor = AGENT_LABEL[steps[steps.length - 1].author] ?? steps[steps.length - 1].author;

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
        <div className="border-t border-border bg-bg-base">
          <Accordion type="multiple" className="px-1">
            {steps.map((s) => (
              <AccordionItem key={s.id} value={s.id} className="border-border">
                <AccordionTrigger className="px-2 py-2 hover:no-underline hover:bg-bg-hover rounded-md">
                  <StepHeader step={s} />
                </AccordionTrigger>
                <AccordionContent>
                  <StepBody step={s} />
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      )}
    </div>
  );
}
