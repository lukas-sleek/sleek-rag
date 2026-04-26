"use client";
import * as React from "react";
import type { Session } from "@supabase/supabase-js";
import { Icon } from "./icons";
import { LoginScreen } from "./login";
import { Sidebar } from "./sidebar";
import { Composer, EmptyState, Message } from "./chat";
import { ProjectFilesModal } from "./project-files-modal";
import { TemplateAnalysisModal } from "./template-modal";
import {
  ACCEPT_ATTR,
  filterAllowedFiles,
  inferFileType,
  mockAnalysis,
  type FileItem,
  type Message as Msg,
  type Project,
} from "./fixtures";
import { createClient } from "@/lib/supabase/client";
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
  const [session, setSession] = React.useState<Session | null>(null);
  const [authReady, setAuthReady] = React.useState(false);
  const [collapsed, setCollapsed] = React.useState(false);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [activeChatId, setActiveChatId] = React.useState<string>("__empty__");

  const [projectFiles, setProjectFiles] = React.useState<Record<string, FileItem[]>>({});

  const [threads, setThreads] = React.useState<Record<string, Msg[]>>({});
  const [streaming, setStreaming] = React.useState(false);
  const streamRef = React.useRef<{ controller: AbortController | null }>({ controller: null });
  const loadedThreadsRef = React.useRef<Set<string>>(new Set());

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
        setThreads({});
        setProjectFiles({});
        setActiveChatId("__empty__");
      }
    });
    unsub = () => data.subscription.unsubscribe();
    return () => unsub?.();
  }, [supabase]);

  React.useEffect(() => {
    if (!session) return;
    let cancelled = false;
    (async () => {
      const res = await api("/api/projects");
      if (!res.ok) {
        pushToast("Projekte konnten nicht geladen werden.", "warn");
        return;
      }
      const rows: { id: string; name: string; chats: { id: string; title: string }[] }[] =
        await res.json();
      if (cancelled) return;
      setProjects(
        rows.map((r) => ({
          id: r.id,
          name: r.name,
          expanded: true,
          hasFiles: false,
          chats: r.chats ?? [],
        })),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [session, pushToast]);

  React.useEffect(() => {
    if (!session) return;
    if (activeChatId === "__empty__") return;
    if (loadedThreadsRef.current.has(activeChatId)) return;
    loadedThreadsRef.current.add(activeChatId);
    let cancelled = false;
    (async () => {
      const res = await api(`/api/chats/${activeChatId}/messages`);
      if (!res.ok) {
        loadedThreadsRef.current.delete(activeChatId);
        return;
      }
      const msgs: Msg[] = await res.json();
      if (cancelled) return;
      setThreads((prev) => ({ ...prev, [activeChatId]: msgs }));
    })();
    return () => {
      cancelled = true;
    };
  }, [session, activeChatId]);

  const activeProjectId = React.useMemo(() => {
    for (const p of projects) {
      if (p.chats.some((c) => c.id === activeChatId)) return p.id;
    }
    return null;
  }, [projects, activeChatId]);

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
      }>;
      if (cancelled) return;
      setProjectFiles((prev) => ({
        ...prev,
        [activeProjectId]: rows.map(rowToFileItem),
      }));
    })();
    return () => {
      cancelled = true;
    };
  }, [session, showFiles.open, activeProjectId]);

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
  }): FileItem => ({
    id: row.id,
    name: row.filename,
    size: formatBytes(row.size_bytes ?? null),
    type: inferFileType(row.filename),
    pages: 1,
    status: row.status === "indexed" ? "complete" : "analyzing",
    analysis: null,
  });

  const uploadProjectFiles = async (projectId: string, accepted: File[]) => {
    if (!accepted.length) return;
    const placeholders: FileItem[] = accepted.map((f, i) => ({
      id: "uploading-" + Date.now() + "-" + i,
      name: f.name,
      size: formatBytes(f.size),
      type: inferFileType(f.name),
      pages: 1,
      status: "analyzing",
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
            throw new Error("upload failed");
          }
          const row = (await res.json()) as {
            id: string;
            filename: string;
            size_bytes?: number | null;
            status: string;
          };
          anySucceeded = true;
          setProjectFiles((prev) => ({
            ...prev,
            [projectId]: (prev[projectId] || []).map((existing) =>
              existing.id === placeholderId ? rowToFileItem(row) : existing,
            ),
          }));
          if (row.status !== "indexed") {
            pushToast(`„${row.filename}“: Status ${row.status}.`, "warn");
          }
        } catch {
          pushToast(`„${f.name}“ konnte nicht hochgeladen werden.`, "warn");
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
    setActiveChatId(row.id);
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
    const res = await api(`/api/chats/${chatId}`, { method: "DELETE" });
    if (!res.ok) {
      pushToast("Chat konnte nicht gelöscht werden.", "warn");
      return;
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
      setActiveChatId(flat[0]?.id || "__empty__");
    }
  };

  const onDeleteChat = (projectId: string, chatId: string, title: string) => {
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
      setActiveChatId(remaining[0]?.id || "__empty__");
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
      setActiveChatId(row.id);
    }

    setPaddedChatId(chatId);
    postSendScrollPendingRef.current = true;

    setThreads((prev) => ({
      ...prev,
      [chatId]: [...(prev[chatId] || []), { role: "user", content: text }],
    }));

    setStreaming(true);
    const controller = new AbortController();
    streamRef.current.controller = controller;

    setThreads((prev) => ({
      ...prev,
      [chatId]: [...(prev[chatId] || []), { role: "assistant", content: "" }],
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

    try {
      const res = await api(`/api/chats/${chatId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        pushToast("Nachricht konnte nicht gesendet werden.", "warn");
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
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
            const { delta } = JSON.parse(data) as { delta: string };
            setThreads((prev) => {
              const arr = [...(prev[chatId] || [])];
              const last = arr[arr.length - 1];
              arr[arr.length - 1] = { role: "assistant", content: (last?.content ?? "") + delta };
              return { ...prev, [chatId]: arr };
            });
          } catch {
            /* ignore malformed line */
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        pushToast("Streaming abgebrochen.", "warn");
      }
    } finally {
      setStreaming(false);
      streamRef.current.controller = null;
    }

    const proj = projects.find((p) => p.chats.some((c) => c.id === chatId));
    const chat = proj?.chats.find((c) => c.id === chatId);
    if (chat && (chat.title === "Neuer Chat" || chat.title === "New chat")) {
      const newTitle = text.slice(0, 40);
      setProjects((prev) =>
        prev.map((p) => ({
          ...p,
          chats: p.chats.map((c) => (c.id === chatId ? { ...c, title: newTitle } : c)),
        })),
      );
      api(`/api/chats/${chatId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle }),
      }).catch(() => {/* best-effort */});
    }
  };

  const onStop = () => {
    streamRef.current.controller?.abort();
    streamRef.current.controller = null;
    setStreaming(false);
  };

  if (!authReady) return null;
  if (!session) return <LoginScreen />;

  return (
    <div className="flex h-screen w-screen bg-bg">
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        projects={projects}
        setProjects={setProjects}
        activeChatId={activeChatId}
        setActiveChatId={(id) => setActiveChatId(id)}
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
              setActiveChatId(chat.id);
            },
          });
        }}
        onRenameChat={onRenameChat}
        onDeleteChat={onDeleteChat}
        onRenameProject={onRenameProject}
        onDeleteProject={onDeleteProject}
        user={{ email: session.user.email ?? "" }}
        onOpenTemplate={() => setShowTemplate(true)}
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
            {isEmpty ? "Neuer Chat" : activeChat?.title}
            {!isEmpty && activeChat && (
              <span className="text-xs text-text-tertiary font-mono tracking-[0.02em]">
                {"  ·  " + activeChat.projectName}
              </span>
            )}
          </div>
          <div className="inline-flex items-center gap-1.5 bg-bg-input border border-border px-2.5 py-[5px] rounded-full text-xs text-text-secondary">
            <span className="w-1.5 h-1.5 rounded-full bg-accent" />
            EAG LLM · gpt-4o
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

        {isEmpty ? (
          <>
            <EmptyState
              onSuggest={(t) => sendMessage(t)}
              hasFiles={activeChat ? activeChat.projectHasFiles !== false : true}
              projectName={activeChat?.projectName}
              onAddFiles={() => {
                if (hiddenFileInputRef.current) hiddenFileInputRef.current.click();
              }}
            />
            <Composer onSend={sendMessage} streaming={streaming} onStop={onStop} />
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
                    streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
                  />
                ))}
              </div>
            </div>
            <Composer onSend={sendMessage} streaming={streaming} onStop={onStop} />
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
        />
      )}

      <TemplateAnalysisModal
        open={showTemplate}
        onClose={() => setShowTemplate(false)}
        onSaved={() => pushToast("Vorlage gespeichert.", "success")}
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
