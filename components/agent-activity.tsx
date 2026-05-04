"use client";
import * as React from "react";
import {
  BotIcon,
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
};

function KindIcon({ kind }: { kind: TraceStep["kind"] }) {
  if (kind === "tool_call") return <HammerIcon className="size-3.5" />;
  if (kind === "tool_response") return <CheckCircle2Icon className="size-3.5" />;
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

// Confidence on retrieved chunks comes from Vertex's grounding metadata —
// specifically the per-chunk maximum across grounding_supports.confidence_
// scores (the same number Agent Builder displays). It's a similarity-style
// signal in [0, 1] where higher = the model leaned harder on this chunk
// when grounding the answer.
//   >= 0.70  → green (high-confidence grounding)
//   >= 0.40  → amber (medium)
//   <  0.40  → rose (low)
function confidenceBadgeProps(confidence: number): {
  variant: "default" | "secondary" | "outline";
  className: string;
} {
  if (confidence >= 0.70) {
    return {
      variant: "default",
      className: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    };
  }
  if (confidence >= 0.40) {
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
      : confidenceBadgeProps(chunk.score);
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
          title="Konfidenz aus grounding_supports (höher = relevanter)"
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

type TraceNode = TraceStep & { children: TraceNode[] };

// Build a 1-deep tree from the flat trace stream so sub-agent steps
// render NESTED inside their parent orchestrator-call's body. The
// retrieval (search_project_documents) is logically *part of* the
// rag_specialist invocation — the model thinks → retrieves → thinks
// → answers in one inference — so the chunk list belongs between the
// parent's `Argumente` and `Antwort`, not as a sibling row below.
//
// Rule: orchestrator-authored frames are top-level. Anything else
// attaches to the most recent top-level frame above it. That covers
// the simple rag_specialist case AND the dispatch_rag_questions
// fan-out (where parallel rag_specialist runs all nest under the
// dispatch row that spawned them).
function buildTree(steps: TraceStep[]): TraceNode[] {
  const top: TraceNode[] = [];
  let lastTop: TraceNode | null = null;
  for (const s of steps) {
    const node: TraceNode = { ...s, children: [] };
    if (s.author === "chat_orchestrator") {
      top.push(node);
      lastTop = node;
    } else if (lastTop) {
      lastTop.children.push(node);
    } else {
      // No orchestrator frame yet — emit at top level so we never
      // silently lose a step.
      top.push(node);
    }
  }
  // Synthesise a `search_project_documents` placeholder child while a
  // rag_specialist call is still `laeuft`. The real retrieval trace is
  // emitted by streaming_agent_tool only AFTER the sub-agent's
  // grounding metadata is available (at end of inference), so without
  // this the user sees Argumente land, then a long pause with no sign
  // that retrieval is in flight, then the chunks pop in. With it,
  // the nested row appears immediately in `laeuft` state and gets
  // overwritten by the real chunked row when it arrives.
  for (const parent of top) {
    if (
      parent.name === "rag_specialist" &&
      parent.kind === "tool_call" &&
      !parent.children.some((c) => c.name === "search_project_documents")
    ) {
      parent.children.push({
        id: `placeholder-search-${parent.id}`,
        author: "rag_specialist",
        kind: "tool_call",
        name: "search_project_documents",
        children: [],
      });
    }
  }
  return top;
}

function ChunkBlock({ step }: { step: TraceStep }) {
  // Render a step's grounded chunks. Only chunks with a non-null
  // score were actually used to back an answer span — unscored chunks
  // were just top-k context and would inflate the "Treffer" count.
  const grounded = (step.chunks || []).filter((c) => c.score != null);
  if (grounded.length === 0) {
    return (
      <div className="text-[12px] text-text-tertiary italic">
        Keine grundenden Treffer
        {step.status && step.status !== "ok" ? ` (${step.status})` : ""}.
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1 flex items-center gap-2">
        <span>Treffer</span>
        <span className="text-text-tertiary">·</span>
        <span>{grounded.length}</span>
        <span className="text-text-tertiary normal-case tracking-normal italic ml-auto">
          Konfidenz ↑ besser
        </span>
        {step.status && step.status !== "ok" && (
          <span className="text-text-tertiary italic">({step.status})</span>
        )}
      </div>
      <div className="space-y-1.5">
        {grounded.map((c) => (
          <ChunkRow key={`${step.id}-${c.idx}`} chunk={c} />
        ))}
      </div>
    </div>
  );
}

function PreBlock({ label, body }: { label: string; body: string }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1.5">
        {label}
      </div>
      <pre className="bg-bg-input border border-border rounded-md p-2.5 text-[11.5px] leading-[1.5] font-mono text-text-secondary whitespace-pre-wrap break-words">
        {body}
      </pre>
    </div>
  );
}

function StepRow({ node }: { node: TraceNode }) {
  return (
    <AccordionItem value={node.id} className="border-border">
      <AccordionTrigger className="px-2 py-2 hover:no-underline hover:bg-bg-hover rounded-md">
        <StepHeader step={node} />
      </AccordionTrigger>
      <AccordionContent>
        <StepBody node={node} />
      </AccordionContent>
    </AccordionItem>
  );
}

function StepBody({ node }: { node: TraceNode }) {
  // Leaf-style render: a step that *only* carries chunks (e.g. the
  // synthesised search_project_documents tool_response) shows its
  // grounded chunk list and nothing else.
  const isPureChunkLeaf =
    node.chunks &&
    !node.args &&
    !node.response &&
    !node.text &&
    node.children.length === 0;
  if (isPureChunkLeaf) {
    return (
      <div className="px-3 pb-3">
        <ChunkBlock step={node} />
      </div>
    );
  }

  const hasArgs = !!node.args;
  const hasResponse = !!node.response;
  const hasText = !!node.text;
  const hasChunks = !!node.chunks;
  const hasChildren = node.children.length > 0;

  if (
    !hasArgs &&
    !hasResponse &&
    !hasText &&
    !hasChunks &&
    !hasChildren
  ) {
    // In-flight placeholder (synthesised search_project_documents row
    // while retrieval is still running, or any tool_call before its
    // body has streamed). Reuses the `chat-dot` keyframe defined in
    // app/globals.css so the dots match the assistant's pre-first-
    // delta thinking indicator in components/chat.tsx exactly.
    return (
      <div
        className="px-3 pb-3 flex items-center gap-1.5"
        role="status"
        aria-label="Schritt laeuft"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:0ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:150ms]" />
        <span className="w-1.5 h-1.5 rounded-full bg-text-tertiary animate-[chat-dot_1s_infinite] [animation-delay:300ms]" />
      </div>
    );
  }

  return (
    <div className="px-3 pb-3 space-y-4">
      {hasArgs && <PreBlock label="Argumente" body={prettyJSON(node.args)} />}
      {/* Sub-agent activity (e.g. search_project_documents under
          rag_specialist) renders chronologically between the parent's
          input and output — search runs before the answer is formed
          inside the same inference. */}
      {hasChildren && (
        <Accordion type="multiple" className="border border-border rounded-md bg-bg-base px-1">
          {node.children.map((c) => (
            <StepRow key={c.id} node={c} />
          ))}
        </Accordion>
      )}
      {hasChunks && !isPureChunkLeaf && <ChunkBlock step={node} />}
      {hasResponse && <PreBlock label="Antwort" body={prettyJSON(node.response)} />}
      {hasText && <PreBlock label="Inhalt" body={node.text!} />}
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
  // Default collapsed even during streaming — the per-step status pill on
  // the header bar already conveys live progress; the user opts in if they
  // want to drill into individual frames.
  const [open, setOpen] = React.useState(false);

  if (!steps.length) return null;
  const stepCount = steps.length;
  // Aggregate header status: streaming → läuft; finished with any errored
  // step → fehler; otherwise → fertig. Mirrors the per-step pill semantics
  // so users get the same signal at a glance from the collapsed header.
  const hasErroredStep = steps.some(
    (s) => s.kind === "tool_response" && s.status === "error",
  );
  const headerPhase: "start" | "done" | "error" = streaming
    ? "start"
    : hasErroredStep
      ? "error"
      : "done";

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
        {headerPhase === "start" && (
          <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-amber-300">
            <Loader2Icon className="size-3 animate-spin" />
            laeuft
          </span>
        )}
        {headerPhase === "done" && (
          <span className="ml-auto inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-emerald-300">
            <CheckCircle2Icon className="size-3" />
            fertig
          </span>
        )}
        {headerPhase === "error" && (
          <span className="ml-auto text-[10.5px] uppercase tracking-wider text-rose-300">
            fehler
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-border bg-bg-base">
          <Accordion type="multiple" className="px-1">
            {buildTree(steps).map((node) => (
              <StepRow key={node.id} node={node} />
            ))}
          </Accordion>
        </div>
      )}
    </div>
  );
}
