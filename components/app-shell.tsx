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
    <div className="app">
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
        className="main"
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
        <div className="topbar">
          <div className="topbar-title">
            {isEmpty ? "Neuer Chat" : activeChat?.title}
            {!isEmpty && activeChat && (
              <span className="topbar-project">
                {"  ·  " + activeChat.projectName}
              </span>
            )}
          </div>
          <div className="model-pill">
            <span className="pulse" />
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
            <div className="thread">
              <div className="thread-inner">
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
        <div className="chat-drop-overlay">
          <div className="chat-drop-inner">
            <Icon.UploadCloud />
            <div className="chat-drop-title">Dateien hier ablegen</div>
          </div>
        </div>
      )}

      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={"toast toast-" + t.kind}>
            <span className="toast-icon"><Icon.AlertCircle /></span>
            <span className="toast-msg">{t.message}</span>
          </div>
        ))}
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
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-title">{title}</div>
        <div className="confirm-body">{body}</div>
        <div className="confirm-actions">
          <button className="btn-ghost" onClick={onCancel}>Abbrechen</button>
          <button className="btn-danger" onClick={onConfirm} autoFocus>
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
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="confirm-title">{title}</div>
        {label && <div className="prompt-label">{label}</div>}
        <input
          ref={inputRef}
          className="prompt-input"
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
        <div className="confirm-actions">
          <button className="btn-ghost" onClick={onCancel}>Abbrechen</button>
          <button
            className="btn-primary"
            onClick={submit}
            disabled={!value.trim()}
          >
            {confirmLabel || "Erstellen"}
          </button>
        </div>
      </div>
    </div>
  );
}
