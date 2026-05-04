import type { Citation, RetrievalChunk, TraceStep } from "@/components/fixtures";
import { createClient } from "./client";

// Vertex serverless flow emits queued | parsing | ready | failed.
// Legacy strings (uploading, embedding, pending, indexed, complete) are
// retained as accepted values so rows from older project versions continue
// to render correctly.
export type FileStatus =
  | "queued"
  | "uploading"
  | "parsing"
  | "embedding"
  | "ready"
  | "failed"
  | "pending"
  | "indexed"
  | "complete";

export type FileStatusUpdate = {
  id: string;
  status: FileStatus;
  ingest_error: string | null;
};

export function subscribeToFileStatus(
  projectId: string,
  onUpdate: (row: FileStatusUpdate) => void,
) {
  const supabase = createClient();
  const channel = supabase
    .channel(`project-files-${projectId}`)
    .on(
      "postgres_changes",
      {
        event: "UPDATE",
        schema: "public",
        table: "project_files",
        filter: `project_id=eq.${projectId}`,
      },
      (payload) => onUpdate(payload.new as FileStatusUpdate),
    )
    .subscribe();
  return () => {
    supabase.removeChannel(channel);
  };
}

// ---------- Assistant turn streaming ----------
//
// The backend persists each generation event into chat_message_deltas (one
// row per ADK event), then UPDATEs chat_messages on completion (status flips
// to 'done' or 'error', content + citations populated). The frontend
// subscribes via Realtime and reconstructs the in-flight turn from the
// stored deltas — closing the tab no longer loses the answer because
// generation runs as a backend background task independent of the request.

export type DeltaPayload =
  | { type: "delta"; content: string }
  | {
      type: "trace";
      id: string;
      author: string;
      kind: TraceStep["kind"];
      name?: string | null;
      args?: string | null;
      response?: string | null;
      text?: string | null;
      chunks?: RetrievalChunk[] | null;
      status?: string | null;
    }
  | { type: "meta"; citations: Citation[]; content?: string }
  | { type: "done" }
  | { progress: { done: number; total: number; question?: string } };

export type AssistantTerminal = {
  status: "done" | "error";
  content: string;
  citations: Citation[] | null;
  error: string | null;
};

export function attachToAssistantStream(
  messageId: string,
  onDelta: (payload: DeltaPayload) => void,
  onTerminal: (terminal: AssistantTerminal) => void,
): () => void {
  const supabase = createClient();
  // Buffer events that arrive between channel subscription and the initial
  // catch-up SELECT, so we don't apply them out of order.
  type Row = { seq: number; payload: DeltaPayload };
  const buffer: Row[] = [];
  let replayed = false;
  let maxSeqApplied = 0;
  let terminalSeen = false;

  const apply = (seq: number, payload: DeltaPayload) => {
    if (seq <= maxSeqApplied) return;
    maxSeqApplied = seq;
    onDelta(payload);
  };

  const fireTerminal = (t: AssistantTerminal) => {
    if (terminalSeen) return;
    terminalSeen = true;
    onTerminal(t);
  };

  const channel = supabase
    .channel(`chat-message-${messageId}`)
    .on(
      "postgres_changes",
      {
        event: "INSERT",
        schema: "public",
        table: "chat_message_deltas",
        filter: `message_id=eq.${messageId}`,
      },
      (evt) => {
        const row = evt.new as Row;
        if (!replayed) buffer.push(row);
        else apply(row.seq, row.payload);
      },
    )
    .on(
      "postgres_changes",
      {
        event: "UPDATE",
        schema: "public",
        table: "chat_messages",
        filter: `id=eq.${messageId}`,
      },
      (evt) => {
        const row = evt.new as {
          status: string;
          content: string | null;
          citations: Citation[] | null;
          error: string | null;
        };
        if (row.status === "done" || row.status === "error") {
          fireTerminal({
            status: row.status,
            content: row.content ?? "",
            citations: row.citations,
            error: row.error,
          });
        }
      },
    )
    .subscribe(async (status) => {
      if (status !== "SUBSCRIBED" || replayed) return;
      // Catch-up: pull every delta that already landed for this message,
      // apply in seq order, then drain anything buffered while we waited.
      const { data: existingDeltas } = await supabase
        .from("chat_message_deltas")
        .select("seq,payload")
        .eq("message_id", messageId)
        .order("seq", { ascending: true });
      for (const r of (existingDeltas ?? []) as Row[]) {
        apply(r.seq, r.payload);
      }
      for (const r of buffer) apply(r.seq, r.payload);
      buffer.length = 0;
      replayed = true;

      // The message may have terminated before the channel was ready; check
      // chat_messages directly so we don't hang forever waiting for a CDC
      // event that already fired.
      const { data: msg } = await supabase
        .from("chat_messages")
        .select("status,content,citations,error")
        .eq("id", messageId)
        .single();
      if (msg && (msg.status === "done" || msg.status === "error")) {
        fireTerminal({
          status: msg.status,
          content: msg.content ?? "",
          citations: msg.citations,
          error: msg.error,
        });
      }
    });

  return () => {
    supabase.removeChannel(channel);
  };
}
