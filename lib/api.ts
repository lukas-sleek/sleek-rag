import { createClient } from "@/lib/supabase/client";

export async function api(path: string, init: RequestInit = {}) {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.access_token}`);
  // Relative URL — same origin as the page. In prod, nginx routes /api/* to
  // uvicorn directly. In dev / tunnel access, Next.js rewrites /api/* to the
  // backend (see next.config.mjs). Frees us from host-based DNS gymnastics.
  return fetch(path, { ...init, headers });
}
