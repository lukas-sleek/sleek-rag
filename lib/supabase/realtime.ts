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
