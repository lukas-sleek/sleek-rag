import { createClient } from "./client";

// New ingestion states (plan 11) plus legacy OpenAI states still on existing rows.
export type FileStatus =
  | "uploading"
  | "parsing"
  | "embedding"
  | "ready"
  | "failed"
  | "pending"
  | "indexed";

export type FileStatusUpdate = {
  id: string;
  status: FileStatus;
  chunk_count: number | null;
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
