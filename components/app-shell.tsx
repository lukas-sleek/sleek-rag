"use client";
import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import type { Session } from "@supabase/supabase-js";
import { Icon } from "./icons";
import { LoginScreen } from "./login";
import { Sidebar } from "./sidebar";
import { Composer, EmptyState, Message } from "./chat";

const LAST_CHAT_KEY = "sleek-rag.last-chat-id";
import { ProjectFilesModal } from "./project-files-modal";
import { PdfViewerDialog } from "./pdf-viewer-dialog";
import { TemplateAnalysisModal } from "./template-modal";
import {
  ACCEPT_ATTR,
  filterAllowedFiles,
  inferFileType,
  mockAnalysis,
  type Citation,
  type FileItem,
  type Message as Msg,
  type Project,
} from "./fixtures";
import { createClient } from "@/lib/supabase/client";
import {
  attachToAssistantStream,
  subscribeToFileStatus,
  type AssistantTerminal,
  type DeltaPayload,
} from "@/lib/supabase/realtime";
import { api } from "@/lib/api";

type Toast = { id: string; message: string; kind: string };

const BTN_GHOST =
  "px-3.5 py-2 rounded-[7px] text-[13px] font-medium cursor-pointer " +
  "bg-transparent border border-border text-text " +
  "transition-[background-color,border-color] duration-150 hover:bg-bg-hover";

const BTN_DANGER =
  "px-3.5 py-2 rounded-[7px] text-[13px] font-medium cursor-pointer " +
  "bg-[#d63a3a] border border-[#d63a3a] text-white " +
  "transition-[background-color,border-color] duration-150 hover:bg-[#c02f2f] hover:border-[#c02f2f]";

export function App() {
  const supabase = React.useMemo(() => createClient(), []);
  const router = useRouter();
  const params = useParams<{ chatId?: string | string[] }>();
  const urlChatId = React.useMemo(() => {
    const raw = params?.chatId;
    if (Array.isArray(raw)) return raw[0] ?? null;
    return raw ?? null;
  }, [params]);
  const [session, setSession] = React.useState<Session | null>(null);
  const [authReady, setAuthReady] = React.useState(false);
  const [collapsed, setCollapsed] = React.useState(false);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [activeChatId, setActiveChatId] = React.useState<string>("__empty__");
  const [projectsLoaded, setProjectsLoaded] = React.useState(false);
  const initialRestoreDoneRef = React.useRef(false);

  const selectChat = React.useCallback(
    (id: string) => {
      setActiveChatId(id);
      if (id === "__empty__") {
        if (typeof window !== "undefined") {
          window.localStorage.removeItem(LAST_CHAT_KEY);
        }
        router.replace("/");
      } else {
        if (typeof window !== "undefined") {
          window.localStorage.setItem(LAST_CHAT_KEY, id);
        }
        router.push(`/c/${id}`);
      }
    },
    [router],
  );

  const [projectFiles, setProjectFiles] = React.useState<Record<string, FileItem[]>>({});

  const [threads, setThreads] = React.useState<Record<string, Msg[]>>({});
  // Per-chat streaming state. The Composer / Stop button reads only the
  // active chat's flag, so opening a new chat while another is generating
  // shows the normal Send button — the background stream keeps running and
  // its own chat thread keeps updating.
  const [streamingChats, setStreamingChats] = React.useState<Set<string>>(
    () => new Set(),
  );
  // Generation runs as a backend background task; the frontend just
  // subscribes to chat_message_deltas via Realtime. Keyed by assistant
  // message id so chat switches and tab reopens can resume in-flight turns
  // without spawning a duplicate subscription.
  const streamUnsubsRef = React.useRef<Map<string, () => void>>(new Map());
  const loadedThreadsRef = React.useRef<Set<string>>(new Set());
  // Initial-load gate. Tracks chats whose messages fetch has resolved (success
  // or failure), whether the URL→state effect has picked an active chat, and
  // a sticky "shell is mounted" flag so chat switches after the first paint
  // don't bounce back to the splash.
  const [chatsLoaded, setChatsLoaded] = React.useState<Set<string>>(new Set());
  const [initialPickDone, setInitialPickDone] = React.useState(false);
  const [shellReady, setShellReady] = React.useState(false);

  const [confirmDialog, setConfirmDialog] = React.useState<null | {
    title: string;
    body: React.ReactNode;
    confirmLabel?: string;
    onConfirm: () => void;
  }>(null);
  const [promptDialog, setPromptDialog] = React.useState<null | {
    title: string;
    label?: string;
    placeholder?: string;
    confirmLabel?: string;
    defaultValue?: string;
    onConfirm: (value: string) => void;
  }>(null);
  const [showFiles, setShowFiles] = React.useState({ open: false, autoPicker: false });
  const [showTemplate, setShowTemplate] = React.useState(false);
  const [viewerCitation, setViewerCitation] = React.useState<Citation | null>(null);
  const [chatDragOver, setChatDragOver] = React.useState(false);
  const [toasts, setToasts] = React.useState<Toast[]>([]);
  // chatId of the chat that's currently in "post-send" mode (extra bottom
  // padding so the new assistant message can be pinned ~40% from the top).
  // Cleared when the user navigates away — coming back shows the chat flush
  // above the composer like ChatGPT.
  const [paddedChatId, setPaddedChatId] = React.useState<string | null>(null);
  const hiddenFileInputRef = React.useRef<HTMLInputElement>(null);

  const threadOuterRef = React.useRef<HTMLDivElement>(null);
  const threadInnerRef = React.useRef<HTMLDivElement>(null);
  // While true, the messages-change layout effect skips its auto-follow so
  // the 40%-from-top scroll scheduled inside sendMessage wins.
  const postSendScrollPendingRef = React.useRef(false);

  // Position the last message so its bottom sits ~16px above the viewport
  // bottom — the "you're caught up" anchor.
  const followLastMessage = React.useCallback(() => {
    const outer = threadOuterRef.current;
    const inner = threadInnerRef.current;
    if (!outer || !inner) return;
    const lastMsg = inner.lastElementChild as HTMLElement | null;
    if (!lastMsg) return;
    const lastMsgBottom = lastMsg.offsetTop + lastMsg.offsetHeight;
    outer.scrollTop = Math.max(0, lastMsgBottom - outer.clientHeight + 16);
  }, []);

  // True when the user is parked near the bottom of the latest message.
  // Used to decide whether to auto-follow new tokens.
  const isFollowingLastMessage = React.useCallback(() => {
    const outer = threadOuterRef.current;
    const inner = threadInnerRef.current;
    if (!outer || !inner) return false;
    if (outer.scrollHeight <= outer.clientHeight + 1) return true;
    const lastMsg = inner.lastElementChild as HTMLElement | null;
    if (!lastMsg) return false;
    const lastMsgBottom = lastMsg.offsetTop + lastMsg.offsetHeight;
    const viewportBottom = outer.scrollTop + outer.clientHeight;
    return Math.abs(lastMsgBottom - viewportBottom) < 64;
  }, []);

  const pushToast = React.useCallback((message: string, kind: string = "warn") => {
    const id = "t-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  React.useEffect(() => {
    let unsub: (() => void) | undefined;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      if (!s) {
        setProjects([]);
        setProjectsLoaded(false);
        setThreads({});
        setProjectFiles({});
        setActiveChatId("__empty__");
        setChatsLoaded(new Set());
        setInitialPickDone(false);
        setShellReady(false);
        initialRestoreDoneRef.current = false;
        if (typeof window !== "undefined") {
          window.localStorage.removeItem(LAST_CHAT_KEY);
        }
        router.replace("/");
      }
    });
    unsub = () => data.subscription.unsubscribe();
    return () => unsub?.();
  }, [supabase, router]);

  React.useEffect(() => {
    if (!session) return;
    const ctrl = new AbortController();
    (async () => {
      let res: Response;
      try {
        res = await api("/api/projects", { signal: ctrl.signal });
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        const msg = (err as Error).message || "network error";
        pushToast(`Projekte konnten nicht geladen werden: ${msg}`, "warn");
        setProjectsLoaded(true);
        return;
      }
      if (!res.ok) {
        let detail = `${res.status}`;
        try {
          const body = await res.clone().json();
          if (body?.detail) detail = `${res.status}: ${body.detail}`;
        } catch {}
        pushToast(`Projekte konnten nicht geladen werden (${detail}).`, "warn");
        setProjectsLoaded(true);
        return;
      }
      const rows: {
        id: string;
        name: string;
        has_files: boolean;
        expanded?: boolean;
        chats: { id: string; title: string }[];
      }[] = await res.json();
      if (ctrl.signal.aborted) return;
      setProjects(
        rows.map((r) => ({
          id: r.id,
          name: r.name,
          expanded: r.expanded === true,
          hasFiles: r.has_files,
          chats: r.chats ?? [],
        })),
      );
      setProjectsLoaded(true);
    })();
    return () => ctrl.abort();
  }, [session, pushToast]);

  // URL → state. Runs on initial mount with /c/{id}, on browser back/forward,
  // and after projects load (so we can validate ownership). Also handles the
  // empty-URL case once: restores last chat from localStorage and rewrites the
  // URL. Permission check is implicit — projects only contains the user's own
  // chats, so an unknown id either belongs to someone else or was deleted.
  React.useEffect(() => {
    if (!projectsLoaded) return;
    const ownedIds = new Set(projects.flatMap((p) => p.chats.map((c) => c.id)));
    if (urlChatId) {
      if (ownedIds.has(urlChatId)) {
        if (urlChatId !== activeChatId) setActiveChatId(urlChatId);
        if (typeof window !== "undefined") {
          window.localStorage.setItem(LAST_CHAT_KEY, urlChatId);
        }
      } else {
        // urlChatId points at a chat that's not in the user's project tree.
        // Two cases:
        //  (a) Internal nav in flight — we just selectChat()'d a new id (or
        //      cleared to "__empty__") and the router.push/replace hasn't
        //      landed yet, so urlChatId is the old / deleted one. If
        //      activeChatId is itself a valid chat now, sync the URL silently.
        //      If we already cleared to "__empty__", just send to /.
        //  (b) External dead link (typed/bookmarked/back-button to a deleted
        //      chat). No valid activeChatId → toast and reset.
        if (activeChatId !== "__empty__" && ownedIds.has(activeChatId)) {
          router.replace(`/c/${activeChatId}`);
        } else if (activeChatId === "__empty__") {
          router.replace("/");
        } else {
          pushToast("Chat nicht gefunden.", "warn");
          router.replace("/");
          setActiveChatId("__empty__");
        }
      }
      initialRestoreDoneRef.current = true;
      setInitialPickDone(true);
      return;
    }
    if (initialRestoreDoneRef.current) return;
    initialRestoreDoneRef.current = true;
    const saved =
      typeof window !== "undefined" ? window.localStorage.getItem(LAST_CHAT_KEY) : null;
    if (saved && ownedIds.has(saved)) {
      setActiveChatId(saved);
      router.replace(`/c/${saved}`);
      setInitialPickDone(true);
      return;
    }
    const first = projects.flatMap((p) => p.chats)[0]?.id;
    if (first) {
      setActiveChatId(first);
      router.replace(`/c/${first}`);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(LAST_CHAT_KEY, first);
      }
    }
    setInitialPickDone(true);
  }, [projectsLoaded, projects, urlChatId, router, pushToast, activeChatId]);

  React.useEffect(() => {
    if (!session) return;
    if (activeChatId === "__empty__") return;
    if (loadedThreadsRef.current.has(activeChatId)) return;
    loadedThreadsRef.current.add(activeChatId);
    const ctrl = new AbortController();
    const markLoaded = (id: string) =>
      setChatsLoaded((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    (async () => {
      let res: Response;
      try {
        res = await api(`/api/chats/${activeChatId}/messages`, { signal: ctrl.signal });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          loadedThreadsRef.current.delete(activeChatId);
          markLoaded(activeChatId);
        }
        return;
      }
      if (!res.ok) {
        loadedThreadsRef.current.delete(activeChatId);
        markLoaded(activeChatId);
        return;
      }
      const msgs: Msg[] = await res.json();
      if (ctrl.signal.aborted) return;
      setThreads((prev) => ({ ...prev, [activeChatId]: msgs }));
      markLoaded(activeChatId);
      // Resume any in-flight assistant turns that were generating when the
      // tab closed / user switched chats. attachAssistantStream is
      // idempotent — it no-ops if a subscription for this message id
      // already exists.
      for (const m of msgs) {
        if (m.role === "assistant" && m.status === "streaming" && m.id) {
          setStreamingChats((prev) => {
            if (prev.has(activeChatId)) return prev;
            const next = new Set(prev);
            next.add(activeChatId);
            return next;
          });
          attachAssistantStream(activeChatId, m.id);
        }
      }
    })();
    return () => {
      // On strict-mode remount, drop the dedup entry too so the second
      // mount re-issues the fetch (the first one is now aborted).
      ctrl.abort();
      loadedThreadsRef.current.delete(activeChatId);
    };
  }, [session, activeChatId]);

  // Promote shellReady once: projects loaded, URL→state has picked an active
  // chat, and (if there is one) its messages have resolved. Sticky — chat
  // switches after the first paint never bounce back to the splash.
  React.useEffect(() => {
    if (shellReady) return;
    if (!projectsLoaded || !initialPickDone) return;
    if (activeChatId !== "__empty__" && !chatsLoaded.has(activeChatId)) return;
    setShellReady(true);
  }, [shellReady, projectsLoaded, initialPickDone, activeChatId, chatsLoaded]);

  const activeProjectId = React.useMemo(() => {
    for (const p of projects) {
      if (p.chats.some((c) => c.id === activeChatId)) return p.id;
    }
    return null;
  }, [projects, activeChatId]);

  // Once on initial load: expand the project that contains the active chat
  // (if it isn't already) and persist the change. After that the user owns
  // the expand/collapse state — switching chats does not auto-expand other
  // projects.
  const initialExpandDoneRef = React.useRef(false);
  React.useEffect(() => {
    if (initialExpandDoneRef.current) return;
    if (!projectsLoaded || !initialPickDone) return;
    if (!activeProjectId) {
      initialExpandDoneRef.current = true;
      return;
    }
    initialExpandDoneRef.current = true;
    const proj = projects.find((p) => p.id === activeProjectId);
    if (!proj || proj.expanded) return;
    setProjects((prev) =>
      prev.map((p) => (p.id === activeProjectId ? { ...p, expanded: true } : p)),
    );
    void api(`/api/projects/${activeProjectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expanded: true }),
    });
  }, [projectsLoaded, initialPickDone, activeProjectId, projects]);

  const onToggleProject = React.useCallback(
    async (projectId: string) => {
      let nextExpanded = false;
      setProjects((prev) =>
        prev.map((p) => {
          if (p.id !== projectId) return p;
          nextExpanded = !p.expanded;
          return { ...p, expanded: nextExpanded };
        }),
      );
      const res = await api(`/api/projects/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expanded: nextExpanded }),
      });
      if (!res.ok) {
        pushToast("Status konnte nicht gespeichert werden.", "warn");
      }
    },
    [pushToast],
  );

  const loadedFilesRef = React.useRef<Set<string>>(new Set());
  React.useEffect(() => {
    if (!session) return;
    if (!showFiles.open) return;
    if (!activeProjectId) return;
    if (loadedFilesRef.current.has(activeProjectId)) return;
    loadedFilesRef.current.add(activeProjectId);
    let cancelled = false;
    (async () => {
      const res = await api(`/api/projects/${activeProjectId}/files`);
      if (!res.ok) {
        loadedFilesRef.current.delete(activeProjectId);
        return;
      }
      const rows = (await res.json()) as Array<{
        id: string;
        filename: string;
        size_bytes?: number | null;
        status: string;
        chunk_count?: number | null;
        page_count?: number | null;
        ingest_error?: string | null;
      }>;
      if (cancelled) return;
      // Preserve any in-flight upload placeholders so a GET that resolves
      // while a POST is still pending doesn't wipe the row the user just
      // dropped (e.g. clicking "Dateien hinzufügen" opens the modal AND
      // starts the upload — both fire requests; whichever lands first must
      // not stomp the other).
      setProjectFiles((prev) => {
        const current = prev[activeProjectId] || [];
        const inFlight = current.filter((f) => f.id.startsWith("uploading-"));
        return {
          ...prev,
          [activeProjectId]: [...inFlight, ...rows.map(rowToFileItem)],
        };
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [session, showFiles.open, activeProjectId]);

  React.useEffect(() => {
    if (!session) return;
    if (!activeProjectId) return;
    const projectId = activeProjectId;
    const unsub = subscribeToFileStatus(projectId, (row) => {
      setProjectFiles((prev) => {
        const list = prev[projectId];
        if (!list) return prev;
        const idx = list.findIndex((f) => f.id === row.id);
        if (idx === -1) return prev;
        const ready = row.status === "ready" || row.status === "indexed";
        const failed = row.status === "failed";
        const updated: FileItem = {
          ...list[idx],
          status: ready ? "complete" : failed ? "failed" : "analyzing",
          ingestStatus: row.status,
          ingestError: row.ingest_error ?? null,
        };
        const next = [...list];
        next[idx] = updated;
        return { ...prev, [projectId]: next };
      });
    });
    return unsub;
  }, [session, activeProjectId]);

  const activeChat = React.useMemo(() => {
    for (const p of projects) {
      const c = p.chats.find((x) => x.id === activeChatId);
      if (c)
        return {
          ...c,
          projectId: p.id,
          projectName: p.name,
          projectHasFiles: p.hasFiles !== false,
        };
    }
    return null;
  }, [projects, activeChatId]);

  const formatBytes = (bytes: number | null | undefined) => {
    if (!bytes && bytes !== 0) return "—";
    return bytes / 1024 / 1024 < 1
      ? Math.round(bytes / 1024) + " KB"
      : (bytes / 1024 / 1024).toFixed(1) + " MB";
  };

  const rowToFileItem = (row: {
    id: string;
    filename: string;
    size_bytes?: number | null;
    status: string;
    chunk_count?: number | null;
    page_count?: number | null;
    ingest_error?: string | null;
  }): FileItem => {
    const ready = row.status === "ready" || row.status === "indexed";
    const failed = row.status === "failed";
    return {
      id: row.id,
      name: row.filename,
      size: formatBytes(row.size_bytes ?? null),
      type: inferFileType(row.filename),
      pages: row.page_count ?? 1,
      status: ready ? "complete" : failed ? "failed" : "analyzing",
      ingestStatus: row.status,
      ingestError: row.ingest_error ?? null,
      analysis: null,
    };
  };

  const uploadProjectFiles = async (projectId: string, accepted: File[]) => {
    if (!accepted.length) return;
    const placeholders: FileItem[] = accepted.map((f, i) => ({
      id: "uploading-" + Date.now() + "-" + i,
      name: f.name,
      // size/pages get overwritten with backend truth after upload completes.
      // Until then we drive the row label off ingestStatus="uploading" so the
      // list shows "Hochladen…" instead of stale local size + fake pages=1.
      size: formatBytes(f.size),
      type: inferFileType(f.name),
      pages: 0,
      status: "analyzing",
      ingestStatus: "uploading",
      analysis: null,
    }));
    setProjectFiles((prev) => ({
      ...prev,
      [projectId]: [...placeholders, ...(prev[projectId] || [])],
    }));
    setShowFiles((s) => (s.open ? s : { open: true, autoPicker: false }));

    const placeholderIds = new Set(placeholders.map((p) => p.id));
    let anySucceeded = false;

    await Promise.all(
      accepted.map(async (f, i) => {
        const placeholderId = placeholders[i].id;
        try {
          const form = new FormData();
          form.append("file", f);
          const res = await api(`/api/projects/${projectId}/files`, {
            method: "POST",
            body: form,
          });
          if (!res.ok) {
            let detail = "";
            try {
              const body = (await res.json()) as { detail?: string };
              if (body.detail) detail = String(body.detail);
            } catch {}
            throw new Error(detail || `HTTP ${res.status}`);
          }
          const row = (await res.json()) as {
            id: string;
            filename: string;
            size_bytes?: number | null;
            status: string;
            chunk_count?: number | null;
            page_count?: number | null;
            ingest_error?: string | null;
          };
          anySucceeded = true;
          setProjectFiles((prev) => ({
            ...prev,
            [projectId]: (prev[projectId] || []).map((existing) =>
              existing.id === placeholderId ? rowToFileItem(row) : existing,
            ),
          }));
          if (row.status !== "indexed" && row.status !== "parsing" && row.status !== "queued" && row.status !== "ready") {
            pushToast(`„${row.filename}“: Status ${row.status}.`, "warn");
          }
        } catch (err) {
          const reason = err instanceof Error && err.message ? `: ${err.message}` : "";
          pushToast(`„${f.name}“ konnte nicht hochgeladen werden${reason}`, "warn");
          setProjectFiles((prev) => ({
            ...prev,
            [projectId]: (prev[projectId] || []).filter(
              (existing) => existing.id !== placeholderId,
            ),
          }));
        }
      }),
    );

    // Drop any leftover placeholders that didn't get replaced (defensive).
    setProjectFiles((prev) => ({
      ...prev,
      [projectId]: (prev[projectId] || []).filter(
        (existing) => !placeholderIds.has(existing.id),
      ),
    }));

    if (anySucceeded) {
      setProjects((prev) =>
        prev.map((p) => (p.id === projectId ? { ...p, hasFiles: true } : p)),
      );
    }
  };

  const handleDirectUpload = (fileList: FileList | null) => {
    const { accepted, rejected } = filterAllowedFiles(fileList);
    if (rejected.length) {
      const msg = rejected.length === 1
        ? "„" + rejected[0].name + "“ wird nicht unterstützt."
        : rejected.length + " Dateien werden nicht unterstützt.";
      pushToast(msg);
    }
    if (!accepted.length) return;
    const projectId = activeChat?.projectId;
    if (!projectId) return;
    void uploadProjectFiles(projectId, accepted);
  };

  const messages = threads[activeChatId] || [];
  const isEmpty = activeChatId === "__empty__" || messages.length === 0;
  const noProjects = projectsLoaded && projects.length === 0;
  const activeChatStreaming = streamingChats.has(activeChatId);

  // Chats considered "empty / unused": a freshly created chat the user
  // hasn't sent the first message in. Used to gate the "+ Neuer Chat"
  // button (already have one open) and the per-chat Löschen action (a
  // project must always have ≥1 chat). When messages aren't loaded for a
  // chat we fall back to the title heuristic — chats are auto-renamed off
  // "Neuer Chat" right after the first message lands.
  const emptyChatIds = React.useMemo(() => {
    const set = new Set<string>();
    for (const p of projects) {
      for (const c of p.chats) {
        const t = threads[c.id];
        const isEmptyChat = t !== undefined ? t.length === 0 : c.title === "Neuer Chat";
        if (isEmptyChat) set.add(c.id);
      }
    }
    return set;
  }, [projects, threads]);
  const displayName = React.useMemo(() => {
    const meta = session?.user.user_metadata as
      | { first_name?: string; full_name?: string; name?: string }
      | undefined;
    const first = meta?.first_name?.trim();
    if (first) return first.split(/\s+/)[0];
    const full = meta?.full_name || meta?.name;
    if (full) return full.trim().split(/\s+/)[0];
    const email = session?.user.email;
    if (email) {
      const local = email.split("@")[0];
      return local.charAt(0).toUpperCase() + local.slice(1);
    }
    return "Alex";
  }, [session]);

  // Auto-follow the latest message:
  //   - On every messages change (new message, streaming token), if the user
  //     was already parked at the bottom of the latest message, keep them
  //     parked. If they scrolled up to read older content, leave them alone.
  //   - On composer growth (outer container shrinks), same rule.
  // The "post-send" behavior of pinning the new assistant message to ~40%
  // from the top is handled directly inside sendMessage; this layout effect
  // intentionally does nothing in that case because the user is far from the
  // bottom right after the 40% scroll, so isFollowingLastMessage returns
  // false until the response grows enough to reach the viewport bottom.
  const lastMessageContent = messages[messages.length - 1]?.content ?? "";
  React.useLayoutEffect(() => {
    if (isEmpty) return;
    // sendMessage's 40%-from-top scroll wins this commit cycle.
    if (postSendScrollPendingRef.current) return;
    if (isFollowingLastMessage()) {
      requestAnimationFrame(() => {
        requestAnimationFrame(followLastMessage);
      });
    }
  }, [isEmpty, messages.length, lastMessageContent, followLastMessage, isFollowingLastMessage]);

  React.useEffect(() => {
    const outer = threadOuterRef.current;
    if (!outer) return;
    const ro = new ResizeObserver(() => {
      if (isFollowingLastMessage()) followLastMessage();
    });
    ro.observe(outer);
    return () => ro.disconnect();
  }, [isEmpty, followLastMessage, isFollowingLastMessage]);

  // On chat switch, drop the post-send padding for the previous chat (so
  // returning later shows the conversation flush above the composer) and
  // anchor the bottom of the new thread to the viewport bottom.
  React.useLayoutEffect(() => {
    setPaddedChatId(null);
    requestAnimationFrame(() => {
      requestAnimationFrame(followLastMessage);
    });
  }, [activeChatId, followLastMessage]);

  const onNewChat = async (projectId: string) => {
    const res = await api("/api/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, title: "Neuer Chat" }),
    });
    if (!res.ok) {
      pushToast("Chat konnte nicht erstellt werden.", "warn");
      return;
    }
    const row: { id: string; title: string } = await res.json();
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? { ...p, expanded: true, chats: [{ id: row.id, title: row.title }, ...p.chats] }
          : p,
      ),
    );
    setThreads((prev) => ({ ...prev, [row.id]: [] }));
    loadedThreadsRef.current.add(row.id);
    selectChat(row.id);
  };

  const onRenameChat = async (projectId: string, chatId: string, title: string) => {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? { ...p, chats: p.chats.map((c) => (c.id === chatId ? { ...c, title } : c)) }
          : p,
      ),
    );
    const res = await api(`/api/chats/${chatId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) pushToast("Chat-Titel konnte nicht gespeichert werden.", "warn");
  };

  const doDeleteChat = async (projectId: string, chatId: string) => {
    const proj = projects.find((p) => p.id === projectId);
    const wasLast = !!proj && proj.chats.length === 1;
    const res = await api(`/api/chats/${chatId}`, { method: "DELETE" });
    if (!res.ok) {
      pushToast("Chat konnte nicht gelöscht werden.", "warn");
      return;
    }
    // Project must always have ≥1 chat. If this was the last chat, create
    // the replacement BEFORE mutating local state so the swap commits
    // atomically — no empty-list window where the sidebar shows nothing
    // selected and the URL→state effect could toast against a stale id.
    if (wasLast) {
      const createRes = await api("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId, title: "Neuer Chat" }),
      });
      if (!createRes.ok) {
        pushToast("Chat konnte nicht erstellt werden.", "warn");
        // Fall through and finish the deletion bookkeeping below; the
        // project will end up empty until the user manually creates one.
      } else {
        const row: { id: string; title: string } = await createRes.json();
        setProjects((prev) =>
          prev.map((p) =>
            p.id === projectId
              ? { ...p, expanded: true, chats: [{ id: row.id, title: row.title }] }
              : p,
          ),
        );
        setThreads((prev) => {
          const next = { ...prev };
          delete next[chatId];
          next[row.id] = [];
          return next;
        });
        loadedThreadsRef.current.delete(chatId);
        loadedThreadsRef.current.add(row.id);
        if (activeChatId === chatId) selectChat(row.id);
        return;
      }
    }
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId ? { ...p, chats: p.chats.filter((c) => c.id !== chatId) } : p,
      ),
    );
    setThreads((prev) => {
      const next = { ...prev };
      delete next[chatId];
      return next;
    });
    loadedThreadsRef.current.delete(chatId);
    if (activeChatId === chatId) {
      const flat = projects.flatMap((p) => p.chats).filter((c) => c.id !== chatId);
      selectChat(flat[0]?.id || "__empty__");
    }
  };

  const onDeleteChat = (projectId: string, chatId: string, title: string) => {
    // Empty / unused chats have nothing to lose — skip the confirm dialog.
    if (emptyChatIds.has(chatId)) {
      void doDeleteChat(projectId, chatId);
      return;
    }
    setConfirmDialog({
      title: "Chat löschen?",
      body: (
        <>
          „<strong>{title}“</strong> wird unwiderruflich gelöscht. Verlauf und Nachrichten gehen verloren.
        </>
      ),
      confirmLabel: "Löschen",
      onConfirm: () => doDeleteChat(projectId, chatId),
    });
  };

  const onRenameProject = async (projectId: string, name: string) => {
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, name } : p)));
    const res = await api(`/api/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) pushToast("Projektname konnte nicht gespeichert werden.", "warn");
  };

  const doDeleteProject = async (projectId: string) => {
    const proj = projects.find((p) => p.id === projectId);
    const chatIds = proj ? proj.chats.map((c) => c.id) : [];
    const res = await api(`/api/projects/${projectId}`, { method: "DELETE" });
    if (!res.ok) {
      pushToast("Projekt konnte nicht gelöscht werden.", "warn");
      return;
    }
    setProjects((prev) => prev.filter((p) => p.id !== projectId));
    setThreads((prev) => {
      const next = { ...prev };
      chatIds.forEach((id) => delete next[id]);
      return next;
    });
    chatIds.forEach((id) => loadedThreadsRef.current.delete(id));
    if (chatIds.includes(activeChatId)) {
      const remaining = projects
        .filter((p) => p.id !== projectId)
        .flatMap((p) => p.chats);
      selectChat(remaining[0]?.id || "__empty__");
    }
  };

  const onDeleteProject = (projectId: string, name: string) => {
    const proj = projects.find((p) => p.id === projectId);
    const n = proj ? proj.chats.length : 0;
    setConfirmDialog({
      title: "Projekt löschen?",
      body: (
        <>
          „<strong>{name}“</strong> und{" "}
          {n === 0 ? "alle zugehörigen Chats" : `${n} ${n === 1 ? "Chat" : "Chats"} darin`}{" "}
          werden unwiderruflich gelöscht.
        </>
      ),
      confirmLabel: "Löschen",
      onConfirm: () => doDeleteProject(projectId),
    });
  };

  const sendMessage = async (text: string) => {
    let chatId = activeChatId;
    if (chatId === "__empty__") {
      const firstProject = projects[0];
      if (!firstProject) {
        pushToast("Erst ein Projekt anlegen.", "warn");
        return;
      }
      const createRes = await api("/api/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: firstProject.id, title: "Neuer Chat" }),
      });
      if (!createRes.ok) {
        pushToast("Chat konnte nicht erstellt werden.", "warn");
        return;
      }
      const row: { id: string; title: string } = await createRes.json();
      chatId = row.id;
      setProjects((prev) =>
        prev.map((p) =>
          p.id === firstProject.id
            ? { ...p, chats: [{ id: row.id, title: row.title }, ...p.chats] }
            : p,
        ),
      );
      setThreads((prev) => ({ ...prev, [row.id]: [] }));
      loadedThreadsRef.current.add(row.id);
      selectChat(row.id);
    }

    setPaddedChatId(chatId);
    postSendScrollPendingRef.current = true;

    setThreads((prev) => ({
      ...prev,
      [chatId]: [...(prev[chatId] || []), { role: "user", content: text }],
    }));

    // First user message in a fresh chat: paint the title with the first
    // ~40 chars of the prompt right away so the sidebar reflects something
    // meaningful before the AI title (2-3 words, can take seconds) lands.
    const currentChat = projects
      .find((p) => p.chats.some((c) => c.id === chatId))
      ?.chats.find((c) => c.id === chatId);
    const isFirstTurn =
      !!currentChat &&
      (currentChat.title === "Neuer Chat" || currentChat.title === "New chat");
    if (isFirstTurn) {
      const trimmed = text.trim();
      const placeholder =
        trimmed.length <= 40 ? trimmed : trimmed.slice(0, 40).trimEnd() + "…";
      setProjects((prev) =>
        prev.map((p) => ({
          ...p,
          chats: p.chats.map((c) => (c.id === chatId ? { ...c, title: placeholder } : c)),
        })),
      );
      api(`/api/chats/${chatId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: placeholder }),
      }).catch(() => {});
      // Kick off the AI title (2-3 words) in parallel with the chat stream.
      // The title call is independent — no need to wait for the message
      // generation to finish before we know what to call this chat.
      void generateChatTitle(chatId, text);
    }

    setStreamingChats((prev) => {
      const next = new Set(prev);
      next.add(chatId);
      return next;
    });

    setThreads((prev) => ({
      ...prev,
      [chatId]: [
        ...(prev[chatId] || []),
        { role: "assistant", content: "", status: "streaming" },
      ],
    }));

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const outer = threadOuterRef.current;
        const inner = threadInnerRef.current;
        if (outer && inner) {
          const lastMsg = inner.lastElementChild as HTMLElement | null;
          if (lastMsg) {
            const target = lastMsg.offsetTop - outer.clientHeight * 0.4;
            outer.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
          }
        }
        postSendScrollPendingRef.current = false;
      });
    });

    let assistantId: string | null = null;
    try {
      const res = await api(`/api/chats/${chatId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) {
        pushToast("Nachricht konnte nicht gesendet werden.", "warn");
        setStreamingChats((prev) => {
          if (!prev.has(chatId)) return prev;
          const next = new Set(prev);
          next.delete(chatId);
          return next;
        });
        return;
      }
      const json = (await res.json()) as {
        user_message_id: string;
        assistant_message_id: string;
      };
      assistantId = json.assistant_message_id;
      // Stamp the optimistic assistant placeholder with its real id so the
      // Realtime subscription can be scoped and we can detect duplicates on
      // chat reopen.
      setThreads((prev) => {
        const arr = [...(prev[chatId] || [])];
        const last = arr[arr.length - 1];
        if (last?.role === "assistant") {
          arr[arr.length - 1] = { ...last, id: assistantId! };
        }
        return { ...prev, [chatId]: arr };
      });
    } catch {
      pushToast("Nachricht konnte nicht gesendet werden.", "warn");
      setStreamingChats((prev) => {
        if (!prev.has(chatId)) return prev;
        const next = new Set(prev);
        next.delete(chatId);
        return next;
      });
      return;
    }

    attachAssistantStream(chatId, assistantId);
  };

  // Apply one streaming-delta payload to the in-thread assistant placeholder
  // for `chatId`. Mirrors the SSE switch we used to run inline in sendMessage,
  // but is now also reached on chat-load resume so a single helper covers
  // both code paths.
  const applyDeltaToThread = React.useCallback(
    (chatId: string, assistantId: string, payload: DeltaPayload) => {
      // progress envelope: replaces content with a transient running
      // placeholder while batched RAG fan-out is in flight.
      if ("progress" in payload && payload.progress) {
        const { done, total, question } = payload.progress;
        const tail = question ? ` — zuletzt: ${question.slice(0, 80)}` : "";
        setThreads((prev) => {
          const arr = [...(prev[chatId] || [])];
          const idx = arr.findIndex((m) => m.id === assistantId);
          if (idx < 0) return prev;
          arr[idx] = {
            ...arr[idx],
            role: "assistant",
            content: `_Projektanalyse läuft… ${done}/${total}${tail}_`,
          };
          return { ...prev, [chatId]: arr };
        });
        return;
      }
      if (!("type" in payload)) return;
      switch (payload.type) {
        case "trace": {
          // Debug-only frame: backend gates emission to allowlisted user
          // emails. Append/upsert the step in the assistant turn's traces
          // array; chat.tsx renders the activity panel from that.
          const step: import("./fixtures").TraceStep = {
            id: payload.id,
            author: payload.author,
            kind: payload.kind,
            name: payload.name ?? null,
            args: payload.args ?? null,
            response: payload.response ?? null,
            text: payload.text ?? null,
            chunks: payload.chunks ?? null,
            status: payload.status ?? null,
          };
          setThreads((prev) => {
            const arr = [...(prev[chatId] || [])];
            const idx = arr.findIndex((m) => m.id === assistantId);
            if (idx < 0) return prev;
            const target = arr[idx];
            const existing = target.traces ?? [];
            // Upsert by id: per-question dispatch frames emit the SAME id
            // (e.g. `dispatch-3`) for the start (`laeuft`) and done
            // (`fertig`) phases — merge in place so the activity panel
            // shows one row per question that flips status rather than
            // two separate rows. Merging instead of replacing preserves
            // fields that only the EARLIER frame set: a tool_call carries
            // `args` (the request), a tool_response carries `response`
            // (the answer); the backend doesn't re-send `args` on the
            // response, so a naive replace would drop "Argumente" from
            // the activity panel once the answer arrives. We skip nullish
            // fields from the incoming step so absent values can't shadow
            // values from the prior frame.
            const existingIdx = existing.findIndex((t) => t.id === step.id);
            let nextTraces: typeof existing;
            if (existingIdx >= 0) {
              const merged = { ...existing[existingIdx] } as Record<string, unknown>;
              for (const [k, v] of Object.entries(step)) {
                if (v !== null && v !== undefined) merged[k] = v;
              }
              nextTraces = existing.map((t, i) =>
                i === existingIdx ? (merged as typeof t) : t,
              );
            } else {
              nextTraces = [...existing, step];
            }
            arr[idx] = { ...target, traces: nextTraces };
            return { ...prev, [chatId]: arr };
          });
          return;
        }
        case "meta": {
          // Backend sends an annotated `content` string alongside citations
          // — the streamed deltas don't carry [N] ref markers (those come
          // from grounding_supports, only available post-generation). Swap
          // the streamed content for the annotated version so chat.tsx's
          // [N] linkifier can match.
          setThreads((prev) => {
            const arr = [...(prev[chatId] || [])];
            const idx = arr.findIndex((m) => m.id === assistantId);
            if (idx < 0) return prev;
            arr[idx] = {
              ...arr[idx],
              content: payload.content ?? arr[idx].content,
              citations: payload.citations ?? [],
            };
            return { ...prev, [chatId]: arr };
          });
          return;
        }
        case "delta": {
          const piece = payload.content ?? "";
          if (!piece) return;
          setThreads((prev) => {
            const arr = [...(prev[chatId] || [])];
            const idx = arr.findIndex((m) => m.id === assistantId);
            if (idx < 0) return prev;
            const target = arr[idx];
            const isProgressPlaceholder = target.content?.startsWith(
              "_Projektanalyse läuft",
            );
            arr[idx] = {
              ...target,
              role: "assistant",
              content: isProgressPlaceholder ? piece : (target.content ?? "") + piece,
            };
            return { ...prev, [chatId]: arr };
          });
          return;
        }
        case "done":
          // The chat_messages UPDATE handler (onTerminal) is the
          // authoritative end-of-turn signal — `done` deltas are advisory.
          return;
      }
    },
    [],
  );

  const applyTerminalToThread = React.useCallback(
    (chatId: string, assistantId: string, terminal: AssistantTerminal) => {
      setThreads((prev) => {
        const arr = [...(prev[chatId] || [])];
        const idx = arr.findIndex((m) => m.id === assistantId);
        if (idx < 0) return prev;
        arr[idx] = {
          ...arr[idx],
          role: "assistant",
          content: terminal.content || arr[idx].content,
          citations: terminal.citations ?? arr[idx].citations ?? [],
          status: terminal.status,
          error: terminal.error ?? null,
        };
        return { ...prev, [chatId]: arr };
      });
      setStreamingChats((prev) => {
        if (!prev.has(chatId)) return prev;
        const next = new Set(prev);
        next.delete(chatId);
        return next;
      });
    },
    [],
  );

  const attachAssistantStream = React.useCallback(
    (chatId: string, assistantId: string) => {
      // Idempotent: chat reopen during a still-running turn must not stack
      // multiple subscriptions for the same message.
      if (streamUnsubsRef.current.has(assistantId)) return;
      const unsubscribe = attachToAssistantStream(
        assistantId,
        (payload) => applyDeltaToThread(chatId, assistantId, payload),
        (terminal) => {
          applyTerminalToThread(chatId, assistantId, terminal);
          const u = streamUnsubsRef.current.get(assistantId);
          streamUnsubsRef.current.delete(assistantId);
          u?.();
        },
      );
      streamUnsubsRef.current.set(assistantId, unsubscribe);
    },
    [applyDeltaToThread, applyTerminalToThread],
  );

  // Background AI title generation. Runs in parallel with the chat stream so
  // the sidebar swaps from the prompt-slice placeholder to a 2-3-word title
  // as soon as the title model is done — independent of how long the answer
  // takes. Errors swallowed: placeholder stays as the worst case.
  const generateChatTitle = async (chatId: string, firstMessage: string) => {
    try {
      const res = await api(`/api/chats/${chatId}/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ first_message: firstMessage }),
      });
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let acc = "";
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") break outer;
          try {
            const { delta } = JSON.parse(data) as { delta?: string };
            if (delta) acc += delta;
          } catch {
            /* ignore malformed line */
          }
        }
      }
      const finalTitle = acc.trim().replace(/^["']+|["']+$/g, "").trim();
      if (finalTitle) {
        setProjects((prev) =>
          prev.map((p) => ({
            ...p,
            chats: p.chats.map((c) =>
              c.id === chatId ? { ...c, title: finalTitle } : c,
            ),
          })),
        );
      }
    } catch {
      /* best-effort title generation; leave the placeholder in place */
    }
  };

  const onStop = () => {
    if (!activeChatId) return;
    // Find the in-flight assistant message for the active chat. Generation
    // runs as a backend background task — to actually stop the model we
    // need to ask the backend to cancel it; the Realtime UPDATE that
    // follows the cancellation flips status='error' and triggers the
    // local cleanup via applyTerminalToThread.
    const arr = threads[activeChatId] || [];
    const inFlight = [...arr].reverse().find(
      (m) => m.role === "assistant" && m.status === "streaming" && m.id,
    );
    if (!inFlight?.id) return;
    void api(`/api/chats/messages/${inFlight.id}/cancel`, { method: "POST" });
  };

  const splash = (
    <div className="flex h-screen w-screen items-center justify-center bg-bg">
      <div className="font-display text-[56px] font-extrabold tracking-[-0.04em] text-text">
        EAG <span className="text-accent">LLM</span>
      </div>
    </div>
  );
  // Hold on the splash until everything the shell needs is in hand. Covers
  // pre-auth (where returning null would briefly expose the previous DOM),
  // and the projects+messages waterfall after sign-in.
  if (!authReady) return splash;
  if (!session) return <LoginScreen />;
  if (!shellReady) return splash;

  return (
    <div className="flex h-screen w-screen bg-bg">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        projects={projects}
        setProjects={setProjects}
        activeChatId={activeChatId}
        setActiveChatId={(id) => selectChat(id)}
        onNewChat={onNewChat}
        onNewProject={() => {
          setPromptDialog({
            title: "Neues Projekt",
            label: "Projektname",
            placeholder: "z. B. Q4 Analyse",
            confirmLabel: "Erstellen",
            defaultValue: "",
            onConfirm: async (name: string) => {
              const trimmed = (name || "").trim() || "Neues Projekt";
              const projRes = await api("/api/projects", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: trimmed }),
              });
              if (!projRes.ok) {
                pushToast("Projekt konnte nicht erstellt werden.", "warn");
                return;
              }
              const proj: { id: string; name: string } = await projRes.json();
              const chatRes = await api("/api/chats", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: proj.id, title: "Neuer Chat" }),
              });
              if (!chatRes.ok) {
                pushToast("Chat konnte nicht erstellt werden.", "warn");
                setProjects((prev) => [
                  { id: proj.id, name: proj.name, expanded: true, hasFiles: false, chats: [] },
                  ...prev,
                ]);
                return;
              }
              const chat: { id: string; title: string } = await chatRes.json();
              setProjects((prev) => [
                {
                  id: proj.id,
                  name: proj.name,
                  expanded: true,
                  hasFiles: false,
                  chats: [{ id: chat.id, title: chat.title }],
                },
                ...prev,
              ]);
              setThreads((prev) => ({ ...prev, [chat.id]: [] }));
              loadedThreadsRef.current.add(chat.id);
              selectChat(chat.id);
            },
          });
        }}
        onRenameChat={onRenameChat}
        onDeleteChat={onDeleteChat}
        onRenameProject={onRenameProject}
        onDeleteProject={onDeleteProject}
        onReorderProjects={async (orderedIds) => {
          const res = await api("/api/projects/order", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_ids: orderedIds }),
          });
          if (!res.ok) {
            pushToast("Reihenfolge konnte nicht gespeichert werden.", "warn");
          }
        }}
        onToggleProject={onToggleProject}
        emptyChatIds={emptyChatIds}
        user={{ email: session.user.email ?? "", displayName }}
        onOpenTemplate={() => setShowTemplate(true)}
        onLogout={() => {
          setConfirmDialog({
            title: "Abmelden?",
            body: <>Du wirst von <strong>{session.user.email}</strong> abgemeldet.</>,
            confirmLabel: "Abmelden",
            onConfirm: async () => {
              const { error } = await supabase.auth.signOut();
              if (error) {
                pushToast("Abmelden fehlgeschlagen.", "warn");
              }
            },
          });
        }}
      />

      <main
        className="flex-1 flex flex-col min-w-0 bg-bg"
        onDragEnter={(e) => {
          if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
            setChatDragOver(true);
          }
        }}
        onDragOver={(e) => {
          if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
            e.preventDefault();
          }
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setChatDragOver(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setChatDragOver(false);
          handleDirectUpload(e.dataTransfer.files);
        }}
      >
        <div className="h-14 flex items-center px-4 gap-3 border-b border-border flex-shrink-0">
          <div className="text-sm font-medium text-text flex-1 whitespace-nowrap overflow-hidden text-ellipsis">
            {!projectsLoaded
              ? ""
              : noProjects
              ? "Willkommen"
              : isEmpty
              ? "Neuer Chat"
              : activeChat?.title}
            {!isEmpty && activeChat && (
              <span className="text-xs text-text-tertiary font-mono tracking-[0.02em]">
                {"  ·  " + activeChat.projectName}
              </span>
            )}
          </div>
          <div className="inline-flex items-center gap-1.5 bg-bg-input border border-border px-2.5 py-[5px] rounded-full text-xs text-text-secondary">
            <span className="w-1.5 h-1.5 rounded-full bg-accent" />
            EAG LLM
          </div>
          {activeChat?.projectHasFiles && (
            <button
              className="icon-btn"
              title="Projektdateien"
              onClick={() => setShowFiles({ open: true, autoPicker: false })}
            >
              <Icon.Files />
            </button>
          )}
        </div>

        {!projectsLoaded ? (
          <div className="flex-1" />
        ) : noProjects ? (
          <EmptyState onSuggest={() => {}} userName={displayName} noProjects />
        ) : isEmpty ? (
          <>
            <EmptyState
              onSuggest={(t) => sendMessage(t)}
              hasFiles={activeChat ? activeChat.projectHasFiles !== false : true}
              projectName={activeChat?.projectName}
              userName={displayName}
              onAddFiles={() => {
                if (hiddenFileInputRef.current) hiddenFileInputRef.current.click();
              }}
            />
            <Composer onSend={sendMessage} streaming={activeChatStreaming} onStop={onStop} />
          </>
        ) : (
          <>
            <div ref={threadOuterRef} className="flex-1 overflow-y-auto flex flex-col">
              {/* When the user has just sent a message in this chat, leave
                  60vh of bottom padding so the new assistant message can be
                  pinned ~40% from the top with room to grow into. The
                  padding stays for the rest of the chat session — only
                  navigating away (chat switch) drops it back to the small
                  gutter, so coming back later shows the conversation flush
                  above the composer like ChatGPT. */}
              <div
                ref={threadInnerRef}
                className={
                  "w-full max-w-[760px] mx-auto pt-8 px-6 flex flex-col gap-7 " +
                  (paddedChatId === activeChatId ? "pb-[60vh]" : "pb-[120px]")
                }
              >
                {messages.map((m, i) => (
                  <Message
                    key={i}
                    msg={m}
                    streaming={activeChatStreaming && i === messages.length - 1 && m.role === "assistant"}
                    onCiteClick={(c) => {
                      // Web citations open in a new tab; PDFs open the
                      // in-app GCS-backed viewer.
                      if (c.kind === "web" && c.url) {
                        window.open(c.url, "_blank", "noopener,noreferrer");
                        return;
                      }
                      setViewerCitation(c);
                    }}
                  />
                ))}
              </div>
            </div>
            <Composer onSend={sendMessage} streaming={activeChatStreaming} onStop={onStop} />
          </>
        )}
      </main>

      {confirmDialog && (
        <ConfirmDialog
          title={confirmDialog.title}
          body={confirmDialog.body}
          confirmLabel={confirmDialog.confirmLabel}
          onCancel={() => setConfirmDialog(null)}
          onConfirm={() => {
            confirmDialog.onConfirm();
            setConfirmDialog(null);
          }}
        />
      )}

      {promptDialog && (
        <PromptDialog
          title={promptDialog.title}
          label={promptDialog.label}
          placeholder={promptDialog.placeholder}
          confirmLabel={promptDialog.confirmLabel}
          defaultValue={promptDialog.defaultValue}
          onCancel={() => setPromptDialog(null)}
          onConfirm={(value) => {
            promptDialog.onConfirm(value);
            setPromptDialog(null);
          }}
        />
      )}

      <input
        ref={hiddenFileInputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTR}
        style={{ display: "none" }}
        onChange={(e) => {
          handleDirectUpload(e.target.files);
          e.target.value = "";
        }}
      />

      {showFiles.open && (
        <ProjectFilesModal
          projectName={activeChat?.projectName || "Projekt"}
          projectId={activeChat?.projectId}
          onClose={() => setShowFiles({ open: false, autoPicker: false })}
          autoOpenPicker={showFiles.autoPicker}
          files={activeChat?.projectId ? (projectFiles[activeChat.projectId] || []) : undefined}
          setFiles={
            activeChat?.projectId
              ? (updater) =>
                  setProjectFiles((prev) => ({
                    ...prev,
                    [activeChat.projectId]:
                      typeof updater === "function"
                        ? (updater as (p: FileItem[]) => FileItem[])(prev[activeChat.projectId] || [])
                        : updater,
                  }))
              : undefined
          }
          onAnalysisComplete={() => {
            if (!activeChat?.projectId) return;
            setProjects((prev) =>
              prev.map((p) =>
                p.id === activeChat.projectId ? { ...p, hasFiles: true } : p
              )
            );
          }}
          notify={pushToast}
          onUpload={
            activeChat?.projectId
              ? (files) => uploadProjectFiles(activeChat.projectId, files)
              : undefined
          }
          onPreview={(file) =>
            setViewerCitation({
              chunk_id: `preview-${file.id}`,
              file_id: file.id,
              project_id: activeChat?.projectId ?? null,
              filename: file.name,
              snippet: "",
              score: 0,
            })
          }
        />
      )}

      <TemplateAnalysisModal
        open={showTemplate}
        onClose={() => setShowTemplate(false)}
        onSaved={() => pushToast("Vorlage gespeichert.", "success")}
      />

      <PdfViewerDialog
        citation={viewerCitation}
        onClose={() => setViewerCitation(null)}
      />

      {chatDragOver && (
        <div className="fixed inset-0 bg-[rgba(13,13,13,.85)] [backdrop-filter:blur(6px)] [-webkit-backdrop-filter:blur(6px)] flex items-center justify-center z-[250] pointer-events-none animate-[pf-fade_.12s_ease-out]">
          <div className="flex flex-col items-center gap-4 px-16 py-12 border-2 border-dashed border-accent rounded-[18px] bg-white/[.02] [&_svg]:w-12 [&_svg]:h-12 [&_svg]:text-accent">
            <Icon.UploadCloud />
            <div className="font-display text-[22px] font-semibold text-text tracking-[-.01em]">Dateien hier ablegen</div>
          </div>
        </div>
      )}

      <div className="fixed bottom-5 right-5 flex flex-col gap-2 z-[250] pointer-events-none max-w-[min(420px,calc(100vw-40px))]">
        {toasts.map((t) => {
          const isSuccess = t.kind === "success";
          return (
            <div
              key={t.id}
              className={
                "flex items-start gap-2.5 px-3.5 py-3 rounded-[10px] bg-bg-elevated text-text border border-border " +
                "shadow-[0_12px_28px_rgba(0,0,0,.45),0_2px_6px_rgba(0,0,0,.3)] text-[13px] leading-[1.4] " +
                "pointer-events-auto animate-[toast-in_.2s_ease-out] min-w-[240px] " +
                (isSuccess ? "[border-left:3px_solid_#10b981]" : "[border-left:3px_solid_#f59e0b]")
              }
            >
              <span
                className={
                  "inline-flex items-center justify-center flex-shrink-0 mt-px " +
                  (isSuccess ? "text-[#10b981]" : "text-[#f59e0b]")
                }
              >
                <Icon.AlertCircle />
              </span>
              <span className="flex-1 break-words">{t.message}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onCancel,
  onConfirm,
}: {
  title: string;
  body: React.ReactNode;
  confirmLabel?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter") onConfirm();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, onConfirm]);

  return (
    <div
      className="fixed inset-0 bg-[rgba(15,18,22,0.42)] [backdrop-filter:blur(2px)] flex items-center justify-center z-[200] animate-[fadeIn_.14s_ease-out]"
      onClick={onCancel}
    >
      <div
        className="bg-bg-elevated text-text border border-border rounded-[12px] shadow-[0_18px_50px_rgba(0,0,0,.18),0_4px_12px_rgba(0,0,0,.08)] pt-[22px] px-[22px] pb-[18px] w-full max-w-[400px] animate-[dialogIn_.16s_ease-out]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-base font-semibold text-text mb-2">{title}</div>
        <div className="text-[13.5px] text-text-secondary leading-[1.5] mb-[18px] [&_strong]:text-text [&_strong]:font-semibold">
          {body}
        </div>
        <div className="flex justify-end gap-2">
          <button className={BTN_GHOST} onClick={onCancel}>Abbrechen</button>
          <button className={BTN_DANGER} onClick={onConfirm} autoFocus>
            {confirmLabel || "Löschen"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PromptDialog({
  title,
  label,
  placeholder,
  confirmLabel,
  defaultValue,
  onCancel,
  onConfirm,
}: {
  title: string;
  label?: string;
  placeholder?: string;
  confirmLabel?: string;
  defaultValue?: string;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const [value, setValue] = React.useState(defaultValue || "");
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, []);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const submit = () => {
    if (!value.trim()) return;
    onConfirm(value);
  };

  return (
    <div
      className="fixed inset-0 bg-[rgba(15,18,22,0.42)] [backdrop-filter:blur(2px)] flex items-center justify-center z-[200] animate-[fadeIn_.14s_ease-out]"
      onClick={onCancel}
    >
      <div
        className="bg-bg-elevated text-text border border-border rounded-[12px] shadow-[0_18px_50px_rgba(0,0,0,.18),0_4px_12px_rgba(0,0,0,.08)] pt-[22px] px-[22px] pb-[18px] w-full max-w-[400px] animate-[dialogIn_.16s_ease-out]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-base font-semibold text-text mb-2">{title}</div>
        {label && (
          <div className="text-xs font-medium text-text-secondary mb-1.5 tracking-[0.01em]">{label}</div>
        )}
        <input
          ref={inputRef}
          className="w-full px-3 py-2.5 rounded-[8px] border border-border bg-bg text-text text-sm [font-family:inherit] [outline:none] mb-[18px] transition-[border-color,box-shadow] duration-150 focus:border-accent focus:shadow-[0_0_0_3px_color-mix(in_oklch,var(--accent)_18%,transparent)] placeholder:text-text-tertiary"
          type="text"
          value={value}
          placeholder={placeholder || ""}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="flex justify-end gap-2">
          <button className={BTN_GHOST} onClick={onCancel}>Abbrechen</button>
          <button className="btn-primary" onClick={submit} disabled={!value.trim()}>
            {confirmLabel || "Erstellen"}
          </button>
        </div>
      </div>
    </div>
  );
}
