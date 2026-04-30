"use client";
import * as React from "react";
import {
  BotIcon,
  HammerIcon,
  CheckCircle2Icon,
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
import type { TraceStep } from "./fixtures";

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
  model_text: "denkt nach",
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
  return (
    <div className="flex items-center gap-2 text-left text-[12.5px] w-full">
      <KindIcon kind={step.kind} />
      <span className="font-medium text-text">{author}</span>
      <span className="text-text-tertiary">·</span>
      <span className="text-text-secondary">{detail}</span>
    </div>
  );
}

function StepBody({ step }: { step: TraceStep }) {
  const blocks: Array<{ label: string; body: string }> = [];
  if (step.args) blocks.push({ label: "Argumente", body: prettyJSON(step.args) });
  if (step.response) blocks.push({ label: "Antwort", body: prettyJSON(step.response) });
  if (step.text) blocks.push({ label: "Inhalt", body: step.text });
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
          <div className="text-[10.5px] uppercase tracking-wider text-text-tertiary mb-1">
            {b.label}
          </div>
          <pre className="bg-bg-input border border-border rounded-md p-2.5 text-[11.5px] leading-[1.5] font-mono text-text-secondary overflow-x-auto whitespace-pre-wrap break-words max-h-72">
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
