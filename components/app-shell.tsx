"use client";
import * as React from "react";
import { Icon } from "./icons";
import { LoginScreen, type LoginUser } from "./login";
import { Sidebar } from "./sidebar";
import { Composer, EmptyState, Message } from "./chat";
import { ProjectFilesModal } from "./project-files-modal";
import { TemplateAnalysisModal } from "./template-modal";
import {
  ACCEPT_ATTR,
  filterAllowedFiles,
  inferFileType,
  mockAnalysis,
  PROJECT_B_FILES,
  PROJECTS_INITIAL,
  SAMPLE_FILES,
  SAMPLE_THREAD,
  type FileItem,
  type Message as Msg,
  type Project,
} from "./fixtures";

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
  const [user, setUser] = React.useState<LoginUser | null>(null);
  const [collapsed, setCollapsed] = React.useState(false);
  const [projects, setProjects] = React.useState<Project[]>(PROJECTS_INITIAL);
  const [activeChatId, setActiveChatId] = React.useState<string>(() =>
    PROJECTS_INITIAL[0]?.chats?.[0]?.id || "__empty__"
  );

  const [projectFiles, setProjectFiles] = React.useState<Record<string, FileItem[]>>(() => {
    const m: Record<string, FileItem[]> = {};
    for (const p of PROJECTS_INITIAL) {
      if (!p.hasFiles) { m[p.id] = []; continue; }
      if (p.id === "p-b") m[p.id] = PROJECT_B_FILES;
      else m[p.id] = SAMPLE_FILES;
    }
    return m;
  });

  const [threads, setThreads] = React.useState<Record<string, Msg[]>>(() => ({ ...SAMPLE_THREAD }));
  const [streaming, setStreaming] = React.useState(false);
  const streamRef = React.useRef({ stop: false });

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
  const hiddenFileInputRef = React.useRef<HTMLInputElement>(null);

  const threadOuterRef = React.useRef<HTMLDivElement>(null);
  const threadInnerRef = React.useRef<HTMLDivElement>(null);
  const stickToBottomRef = React.useRef(true);

  const scrollThreadToBottom = React.useCallback(() => {
    const el = threadOuterRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const pushToast = React.useCallback((message: string, kind: string = "warn") => {
    const id = "t-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

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

    const newFiles: FileItem[] = accepted.map((f, i) => ({
      id: "f-" + Date.now() + "-" + i,
      name: f.name,
      size:
        f.size / 1024 / 1024 < 1
          ? Math.round(f.size / 1024) + " KB"
          : (f.size / 1024 / 1024).toFixed(1) + " MB",
      type: inferFileType(f.name),
      pages: 1,
      status: "analyzing",
      analysis: null,
    }));

    setProjectFiles((prev) => ({
      ...prev,
      [projectId]: [...newFiles, ...(prev[projectId] || [])],
    }));
    setShowFiles({ open: true, autoPicker: false });

    const ids = newFiles.map((f) => f.id);
    setTimeout(() => {
      setProjectFiles((prev) => ({
        ...prev,
        [projectId]: (prev[projectId] || []).map((f) =>
          ids.includes(f.id)
            ? {
                ...f,
                status: "complete" as const,
                pages: Math.max(1, Math.round(2 + Math.random() * 14)),
                analysis: mockAnalysis(f.name),
              }
            : f
        ),
      }));
      setProjects((prev) =>
        prev.map((p) => (p.id === projectId ? { ...p, hasFiles: true } : p))
      );
    }, 5000);
  };

  const messages = threads[activeChatId] || [];
  const isEmpty = activeChatId === "__empty__" || messages.length === 0;

  // Sticky-to-bottom scroll for the thread:
  //   - track whether the user is currently parked at the bottom
  //   - if so, follow streaming tokens and stay pinned when the composer grows
  //   - if the user scrolls up to read older messages, leave them alone
  React.useEffect(() => {
    const el = threadOuterRef.current;
    if (!el) return;
    const onScroll = () => {
      const threshold = 48; // px from the bottom counts as "at bottom"
      stickToBottomRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [isEmpty]);

  React.useEffect(() => {
    const outer = threadOuterRef.current;
    const inner = threadInnerRef.current;
    if (!outer || !inner) return;
    const onResize = () => {
      if (stickToBottomRef.current) scrollThreadToBottom();
    };
    const ro = new ResizeObserver(onResize);
    ro.observe(inner); // new messages, streaming tokens
    ro.observe(outer); // composer growth shrinks the thread
    return () => ro.disconnect();
  }, [isEmpty, scrollThreadToBottom]);

  // On chat switch, jump to the bottom of the new thread.
  React.useLayoutEffect(() => {
    stickToBottomRef.current = true;
    requestAnimationFrame(scrollThreadToBottom);
  }, [activeChatId, scrollThreadToBottom]);

  const onNewChat = (projectId: string) => {
    const id = "c-new-" + Date.now();
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? { ...p, expanded: true, chats: [{ id, title: "Neuer Chat" }, ...p.chats] }
          : p
      )
    );
    setThreads((prev) => ({ ...prev, [id]: [] }));
    setActiveChatId(id);
  };

  const onRenameChat = (projectId: string, chatId: string, title: string) => {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId
          ? { ...p, chats: p.chats.map((c) => (c.id === chatId ? { ...c, title } : c)) }
          : p
      )
    );
  };

  const doDeleteChat = (projectId: string, chatId: string) => {
    setProjects((prev) =>
      prev.map((p) =>
        p.id === projectId ? { ...p, chats: p.chats.filter((c) => c.id !== chatId) } : p
      )
    );
    setThreads((prev) => {
      const next = { ...prev };
      delete next[chatId];
      return next;
    });
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

  const onRenameProject = (projectId: string, name: string) => {
    setProjects((prev) => prev.map((p) => (p.id === projectId ? { ...p, name } : p)));
  };

  const doDeleteProject = (projectId: string) => {
    const proj = projects.find((p) => p.id === projectId);
    const chatIds = proj ? proj.chats.map((c) => c.id) : [];
    setProjects((prev) => prev.filter((p) => p.id !== projectId));
    setThreads((prev) => {
      const next = { ...prev };
      chatIds.forEach((id) => delete next[id]);
      return next;
    });
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
      chatId = projects[0]?.chats?.[0]?.id || "c-a1";
      setActiveChatId(chatId);
    }

    // User just submitted — make sure they see their message and the response.
    stickToBottomRef.current = true;

    setThreads((prev) => ({
      ...prev,
      [chatId]: [...(prev[chatId] || []), { role: "user", content: text }],
    }));

    const reply =
      "Looking through your indexed sources for relevant material…\n\n" +
      "Based on what I found, here's a draft response. I've kept it concise and " +
      "grounded in the documents you've uploaded — let me know if you'd like a different tone, " +
      "more detail, or to dig into a specific section.";

    setStreaming(true);
    streamRef.current.stop = false;
    setThreads((prev) => ({
      ...prev,
      [chatId]: [...(prev[chatId] || []), { role: "assistant", content: "" }],
    }));

    let acc = "";
    const chunks = reply.split(" ");
    for (let i = 0; i < chunks.length; i++) {
      if (streamRef.current.stop) break;
      acc += (i === 0 ? "" : " ") + chunks[i];
      setThreads((prev) => {
        const arr = [...(prev[chatId] || [])];
        arr[arr.length - 1] = { role: "assistant", content: acc };
        return { ...prev, [chatId]: arr };
      });
      await new Promise((r) => setTimeout(r, 35 + Math.random() * 50));
    }
    setStreaming(false);

    setProjects((prev) =>
      prev.map((p) => ({
        ...p,
        chats: p.chats.map((c) =>
          c.id === chatId && (c.title === "Neuer Chat" || c.title === "New chat")
            ? { ...c, title: text.slice(0, 40) }
            : c
        ),
      }))
    );
  };

  const onStop = () => {
    streamRef.current.stop = true;
    setStreaming(false);
  };

  if (!user) {
    return <LoginScreen onLogin={setUser} />;
  }

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
            onConfirm: (name: string) => {
              const trimmed = (name || "").trim() || "Neues Projekt";
              const pid = "p-new-" + Date.now();
              const cid = "c-new-" + Date.now();
              setProjects((prev) => [
                {
                  id: pid,
                  name: trimmed,
                  expanded: true,
                  hasFiles: false,
                  chats: [{ id: cid, title: "Neuer Chat" }],
                },
                ...prev,
              ]);
              setThreads((prev) => ({ ...prev, [cid]: [] }));
              setActiveChatId(cid);
            },
          });
        }}
        onRenameChat={onRenameChat}
        onDeleteChat={onDeleteChat}
        onRenameProject={onRenameProject}
        onDeleteProject={onDeleteProject}
        user={user}
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
              <div ref={threadInnerRef} className="w-full max-w-[760px] mx-auto pt-8 pb-[120px] px-6 flex flex-col gap-7">
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
