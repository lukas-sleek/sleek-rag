"use client";
import * as React from "react";
import { Icon } from "./icons";
import type { Project } from "./fixtures";

type Drag =
  | { kind: "project"; projectId: string }
  | { kind: "chat"; projectId: string; chatId: string }
  | null;

let drag: Drag = null;
const setDragRef = (d: Drag) => { drag = d; };
const getDrag = (): Drag => drag;

export type LoginUser = { email: string };

function ContextMenu({
  open,
  onClose,
  onRename,
  onDelete,
  anchorClass,
}: {
  open: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  anchorClass?: string;
}) {
  React.useEffect(() => {
    if (!open) return;
    const close = () => onClose();
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className={"ctx-menu " + (anchorClass || "")} onClick={(e) => e.stopPropagation()}>
      <button className="ctx-item" onClick={() => { onRename(); onClose(); }}>
        <Icon.Edit /> Umbenennen
      </button>
      <button className="ctx-item danger" onClick={() => { onDelete(); onClose(); }}>
        <Icon.Trash /> Löschen
      </button>
    </div>
  );
}

type DragOverChat = { chatId: string; before: boolean } | null;

function ChatItem({
  chat,
  projectId,
  active,
  onSelect,
  onRename,
  onDelete,
  onChatDrop,
  dragOver,
  setDragOverChat,
}: {
  chat: { id: string; title: string };
  projectId: string;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
  onChatDrop: (srcId: string, tgtId: string, before: boolean) => void;
  dragOver: DragOverChat;
  setDragOverChat: (v: DragOverChat) => void;
}) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(chat.title);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commit = () => {
    const v = draft.trim();
    if (v && v !== chat.title) onRename(v);
    setEditing(false);
    setDraft(v || chat.title);
  };

  const onDragStart = (e: React.DragEvent) => {
    if (editing) { e.preventDefault(); return; }
    setDragRef({ kind: "chat", projectId, chatId: chat.id });
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", chat.id);
  };

  const onDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    const d = getDrag();
    if (!d || d.kind !== "chat" || d.projectId !== projectId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    setDragOverChat({ chatId: chat.id, before });
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    const d = getDrag();
    if (!d || d.kind !== "chat" || d.projectId !== projectId) return;
    e.preventDefault();
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    onChatDrop(d.chatId, chat.id, before);
    setDragRef(null);
    setDragOverChat(null);
  };

  const indicator = !!dragOver && dragOver.chatId === chat.id;

  return (
    <div
      className={
        "chat-item-wrap" +
        (active ? " active" : "") +
        (indicator ? (dragOver!.before ? " drop-before" : " drop-after") : "")
      }
      draggable={!editing}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragLeave={() => setDragOverChat(null)}
      onDrop={onDrop}
      onDragEnd={() => { setDragRef(null); setDragOverChat(null); }}
    >
      <button
        className={"chat-item" + (active ? " active" : "")}
        onClick={() => !editing && onSelect()}
      >
        <span className="dot" />
        {editing ? (
          <input
            ref={inputRef}
            className="chat-item-edit"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") { setDraft(chat.title); setEditing(false); }
            }}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="label">{chat.title}</span>
        )}
      </button>
      {!editing && (
        <button
          className={"chat-more" + (menuOpen ? " open" : "")}
          onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
          title="Optionen"
        >
          <Icon.More />
        </button>
      )}
      <ContextMenu
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        onRename={() => { setDraft(chat.title); setEditing(true); }}
        onDelete={onDelete}
      />
    </div>
  );
}

type ProjDragOver = { projectId: string; before: boolean } | null;

function ProjectSection({
  proj,
  activeChatId,
  setActiveChatId,
  onToggle,
  onAdd,
  onRename,
  onDelete,
  onRenameChat,
  onDeleteChat,
  onChatDrop,
  onProjectDragStart,
  onProjectDragOver,
  onProjectDrop,
  onProjectDragEnd,
  projDragOver,
}: {
  proj: Project;
  activeChatId: string;
  setActiveChatId: (id: string) => void;
  onToggle: () => void;
  onAdd: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
  onRenameChat: (projectId: string, chatId: string, title: string) => void;
  onDeleteChat: (projectId: string, chatId: string, title: string) => void;
  onChatDrop: (projectId: string, srcId: string, tgtId: string, before: boolean) => void;
  onProjectDragStart: (e: React.DragEvent, projectId: string) => void;
  onProjectDragOver: (e: React.DragEvent<HTMLDivElement>, projectId: string) => void;
  onProjectDrop: (e: React.DragEvent<HTMLDivElement>, projectId: string) => void;
  onProjectDragEnd: () => void;
  projDragOver: ProjDragOver;
}) {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(proj.name);
  const [dragOverChat, setDragOverChat] = React.useState<DragOverChat>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const commit = () => {
    const v = draft.trim();
    if (v && v !== proj.name) onRename(v);
    setEditing(false);
    setDraft(v || proj.name);
  };

  const projIndicator = !!projDragOver && projDragOver.projectId === proj.id;

  return (
    <div
      className={
        "sidebar-section" +
        (projIndicator ? (projDragOver!.before ? " drop-before" : " drop-after") : "")
      }
      onDragOver={(e) => onProjectDragOver(e, proj.id)}
      onDrop={(e) => onProjectDrop(e, proj.id)}
    >
      <div
        className={"sidebar-section-header" + (proj.expanded ? "" : " collapsed")}
        onClick={() => !editing && onToggle()}
        draggable={!editing}
        onDragStart={(e) => onProjectDragStart(e, proj.id)}
        onDragEnd={onProjectDragEnd}
      >
        <span className="chev"><Icon.Chevron /></span>
        {editing ? (
          <input
            ref={inputRef}
            className="project-name-edit"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") { setDraft(proj.name); setEditing(false); }
            }}
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <span className="name">{proj.name}</span>
        )}

        <div className="proj-actions" onClick={(e) => e.stopPropagation()}>
          <button
            className={"proj-more" + (menuOpen ? " open" : "")}
            onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
            title="Optionen"
          >
            <Icon.More />
          </button>
          <button
            className="add"
            onClick={(e) => { e.stopPropagation(); onAdd(); }}
            title={`Neuer Chat in ${proj.name}`}
          >
            <Icon.Plus />
          </button>
          <ContextMenu
            open={menuOpen}
            onClose={() => setMenuOpen(false)}
            onRename={() => { setDraft(proj.name); setEditing(true); }}
            onDelete={onDelete}
            anchorClass="ctx-menu-proj"
          />
        </div>
      </div>
      {proj.expanded && (
        <div className="chat-list">
          {proj.chats.map((c) => (
            <ChatItem
              key={c.id}
              chat={c}
              projectId={proj.id}
              active={activeChatId === c.id}
              onSelect={() => setActiveChatId(c.id)}
              onRename={(title) => onRenameChat(proj.id, c.id, title)}
              onDelete={() => onDeleteChat(proj.id, c.id, c.title)}
              onChatDrop={(srcId, tgtId, before) => onChatDrop(proj.id, srcId, tgtId, before)}
              dragOver={dragOverChat}
              setDragOverChat={setDragOverChat}
            />
          ))}
          {proj.chats.length === 0 && (
            <div className="chat-list-empty">Keine Chats</div>
          )}
        </div>
      )}
    </div>
  );
}

export function Sidebar({
  collapsed,
  onToggle,
  projects,
  setProjects,
  activeChatId,
  setActiveChatId,
  onNewChat,
  onNewProject,
  onRenameChat,
  onDeleteChat,
  onRenameProject,
  onDeleteProject,
  user,
  onOpenTemplate,
}: {
  collapsed: boolean;
  onToggle: () => void;
  projects: Project[];
  setProjects: React.Dispatch<React.SetStateAction<Project[]>>;
  activeChatId: string;
  setActiveChatId: (id: string) => void;
  onNewChat: (projectId: string) => void;
  onNewProject: () => void;
  onRenameChat: (projectId: string, chatId: string, title: string) => void;
  onDeleteChat: (projectId: string, chatId: string, title: string) => void;
  onRenameProject: (projectId: string, name: string) => void;
  onDeleteProject: (projectId: string, name: string) => void;
  user: LoginUser;
  onOpenTemplate: () => void;
}) {
  const [search, setSearch] = React.useState("");
  const [projDragOver, setProjDragOver] = React.useState<ProjDragOver>(null);

  const toggleProject = (pid: string) => {
    setProjects((prev) =>
      prev.map((p) => (p.id === pid ? { ...p, expanded: !p.expanded } : p))
    );
  };

  const visibleProjects = !search.trim()
    ? projects
    : projects.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));

  const onProjectDragStart = (e: React.DragEvent, projectId: string) => {
    setDragRef({ kind: "project", projectId });
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", projectId);
  };
  const onProjectDragOver = (e: React.DragEvent<HTMLDivElement>, projectId: string) => {
    const d = getDrag();
    if (!d || d.kind !== "project") return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    setProjDragOver({ projectId, before });
  };
  const onProjectDrop = (e: React.DragEvent<HTMLDivElement>, projectId: string) => {
    const d = getDrag();
    if (!d || d.kind !== "project") return;
    e.preventDefault();
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    if (d.projectId !== projectId) {
      setProjects((prev) => {
        const arr = [...prev];
        const fromIdx = arr.findIndex((p) => p.id === d.projectId);
        if (fromIdx === -1) return prev;
        const [moved] = arr.splice(fromIdx, 1);
        let toIdx = arr.findIndex((p) => p.id === projectId);
        if (toIdx === -1) return prev;
        if (!before) toIdx += 1;
        arr.splice(toIdx, 0, moved);
        return arr;
      });
    }
    setDragRef(null);
    setProjDragOver(null);
  };
  const onProjectDragEnd = () => { setDragRef(null); setProjDragOver(null); };

  const onChatDrop = (projectId: string, srcId: string, tgtId: string, before: boolean) => {
    if (srcId === tgtId) return;
    setProjects((prev) =>
      prev.map((p) => {
        if (p.id !== projectId) return p;
        const arr = [...p.chats];
        const fromIdx = arr.findIndex((c) => c.id === srcId);
        if (fromIdx === -1) return p;
        const [moved] = arr.splice(fromIdx, 1);
        let toIdx = arr.findIndex((c) => c.id === tgtId);
        if (toIdx === -1) return p;
        if (!before) toIdx += 1;
        arr.splice(toIdx, 0, moved);
        return { ...p, chats: arr };
      })
    );
  };

  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="sidebar-header">
        {!collapsed && (
          <div className="sidebar-logo">
            EAG <span className="accent">LLM</span>
          </div>
        )}
        <button
          className="icon-btn sidebar-toggle"
          onClick={onToggle}
          title={collapsed ? "Sidebar öffnen" : "Sidebar einklappen"}
        >
          <Icon.SidebarLeft />
        </button>
      </div>

      {collapsed && (
        <div className="sidebar-collapsed-actions">
          <button
            className="icon-btn"
            onClick={() => onNewProject && onNewProject()}
            title="Neues Projekt"
          >
            <Icon.Compose />
          </button>
        </div>
      )}

      {!collapsed && (
        <>
          <button
            className="sidebar-template-btn"
            onClick={() => onOpenTemplate && onOpenTemplate()}
            title="Vorlage Projektanalyse bearbeiten"
          >
            <span className="sidebar-template-icon"><Icon.FileText /></span>
            <span className="sidebar-template-label">Vorlage Analyse</span>
            <span className="sidebar-template-edit"><Icon.Edit /></span>
          </button>

          <div className="sidebar-search">
            <input
              className="sidebar-search-input"
              placeholder="Projekte suchen…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <button
            className="sidebar-new-chat"
            onClick={() => onNewProject && onNewProject()}
          >
            <Icon.PlusBig />
            Neues Projekt
          </button>

          <div className="sidebar-scroll">
            {visibleProjects.map((proj) => (
              <ProjectSection
                key={proj.id}
                proj={proj}
                activeChatId={activeChatId}
                setActiveChatId={setActiveChatId}
                onToggle={() => toggleProject(proj.id)}
                onAdd={() => onNewChat(proj.id)}
                onRename={(name) => onRenameProject(proj.id, name)}
                onDelete={() => onDeleteProject(proj.id, proj.name)}
                onRenameChat={onRenameChat}
                onDeleteChat={onDeleteChat}
                onChatDrop={onChatDrop}
                onProjectDragStart={onProjectDragStart}
                onProjectDragOver={onProjectDragOver}
                onProjectDrop={onProjectDrop}
                onProjectDragEnd={onProjectDragEnd}
                projDragOver={projDragOver}
              />
            ))}
            {visibleProjects.length === 0 && (
              <div className="chat-list-empty">Keine Projekte gefunden</div>
            )}
          </div>
        </>
      )}

      <div className="sidebar-footer">
        <div className="avatar">{(user.email[0] || "A").toUpperCase()}</div>
        {!collapsed && (
          <div className="sidebar-footer-text">
            <div className="name">{user.email.split("@")[0]}</div>
            <div className="email">{user.email}</div>
          </div>
        )}
      </div>
    </aside>
  );
}
