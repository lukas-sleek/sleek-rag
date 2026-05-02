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
  canDelete = true,
}: {
  open: boolean;
  onClose: () => void;
  onRename: () => void;
  onDelete: () => void;
  anchorClass?: string;
  canDelete?: boolean;
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
      {canDelete && (
        <button className="ctx-item danger" onClick={() => { onDelete(); onClose(); }}>
          <Icon.Trash /> Löschen
        </button>
      )}
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
  canDelete,
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
  canDelete: boolean;
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
          <span className="label">{chat.title || "​"}</span>
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
        canDelete={canDelete}
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
  isDragSource,
  emptyChatIds,
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
  isDragSource: boolean;
  emptyChatIds: Set<string>;
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
        "sidebar-section mt-3" +
        (projIndicator ? (projDragOver!.before ? " drop-before" : " drop-after") : "") +
        (isDragSource ? " dragging-source" : "")
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
          {!proj.chats.some((c) => emptyChatIds.has(c.id)) && (
            <button
              className="add"
              onClick={(e) => { e.stopPropagation(); onAdd(); }}
              title={`Neuer Chat in ${proj.name}`}
            >
              <Icon.Plus />
            </button>
          )}
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
              canDelete={!(proj.chats.length === 1 && emptyChatIds.has(c.id))}
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
  onReorderProjects,
  onToggleProject,
  emptyChatIds,
  user,
  onOpenTemplate,
  onLogout,
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
  onReorderProjects: (orderedIds: string[]) => void;
  onToggleProject: (projectId: string) => void;
  emptyChatIds: Set<string>;
  user: LoginUser;
  onOpenTemplate: () => void;
  onLogout: () => void;
}) {
  const [search, setSearch] = React.useState("");
  const [projDragOver, setProjDragOver] = React.useState<ProjDragOver>(null);
  const [userMenuOpen, setUserMenuOpen] = React.useState(false);
  React.useEffect(() => {
    if (!userMenuOpen) return;
    const close = () => setUserMenuOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [userMenuOpen]);
  // Source of an active project drag. Set after the browser has captured the
  // drag image (via rAF) so flipping the .dragging-source class doesn't
  // cancel the drag. Cleared on dragend / drop / non-drop release.
  const [draggingProjectId, setDraggingProjectId] = React.useState<string | null>(null);

  const visibleProjects = !search.trim()
    ? projects
    : projects.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));

  const onProjectDragStart = (e: React.DragEvent, projectId: string) => {
    setDragRef({ kind: "project", projectId });
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", projectId);
    // Defer so the browser snapshots the drag image at full size before we
    // collapse the source.
    requestAnimationFrame(() => setDraggingProjectId(projectId));
  };
  const onProjectDragOver = (e: React.DragEvent<HTMLDivElement>, projectId: string) => {
    const d = getDrag();
    if (!d || d.kind !== "project") return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    // Suppress the indicator for moves that resolve to a no-op: dropping on
    // yourself, or above the immediate neighbour below, or below the
    // immediate neighbour above. All three insert at the source's current
    // index after removal.
    const fromIdx = projects.findIndex((p) => p.id === d.projectId);
    const toIdx = projects.findIndex((p) => p.id === projectId);
    const isNoOp =
      d.projectId === projectId ||
      fromIdx === -1 ||
      toIdx === -1 ||
      (before && toIdx === fromIdx + 1) ||
      (!before && toIdx === fromIdx - 1);
    if (isNoOp) {
      if (projDragOver) setProjDragOver(null);
      return;
    }
    setProjDragOver({ projectId, before });
  };
  const onProjectDrop = (e: React.DragEvent<HTMLDivElement>, projectId: string) => {
    const d = getDrag();
    if (!d || d.kind !== "project") return;
    e.preventDefault();
    const r = e.currentTarget.getBoundingClientRect();
    const before = e.clientY < r.top + r.height / 2;
    if (d.projectId !== projectId) {
      // Compute the new order outside the updater. setProjects's updater
      // doesn't run until the next render commit, so reading a closure
      // variable mutated inside it would observe null here and skip the
      // server PUT — that's why the first drop "didn't stick" on reload.
      const arr = [...projects];
      const fromIdx = arr.findIndex((p) => p.id === d.projectId);
      if (fromIdx !== -1) {
        const [moved] = arr.splice(fromIdx, 1);
        let toIdx = arr.findIndex((p) => p.id === projectId);
        if (toIdx !== -1) {
          if (!before) toIdx += 1;
          arr.splice(toIdx, 0, moved);
          setProjects(arr);
          onReorderProjects(arr.map((p) => p.id));
        }
      }
    }
    setDragRef(null);
    setProjDragOver(null);
    setDraggingProjectId(null);
  };
  const onProjectDragEnd = () => {
    setDragRef(null);
    setProjDragOver(null);
    setDraggingProjectId(null);
  };

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
    <aside
      className={
        "flex flex-col flex-shrink-0 bg-bg-sidebar border-r border-border " +
        "overflow-hidden transition-[width] duration-200 ease-[cubic-bezier(0.4,0,0.2,1)] " +
        (collapsed ? "w-14" : "w-[280px]")
      }
    >
      <div className="h-14 flex items-center px-3 gap-2 border-b border-border flex-shrink-0">
        {!collapsed && (
          <div className="font-display text-[17px] font-extrabold tracking-[-0.03em] flex-1 whitespace-nowrap overflow-hidden">
            EAG <span className="text-accent">LLM</span>
          </div>
        )}
        <button
          className="icon-btn ml-auto"
          onClick={onToggle}
          title={collapsed ? "Sidebar öffnen" : "Sidebar einklappen"}
        >
          <Icon.SidebarLeft />
        </button>
      </div>

      {collapsed && (
        <div className="flex flex-col items-center py-2 gap-1">
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
            className="group flex items-center gap-2.5 w-[calc(100%-16px)] mx-2 mb-2 px-3 py-2.5 bg-transparent border border-border rounded-[10px] text-text-secondary text-[13px] font-medium tracking-[-0.01em] text-left transition-[background-color,border-color,color] duration-150 hover:bg-bg-hover hover:border-border-strong hover:text-text"
            onClick={() => onOpenTemplate && onOpenTemplate()}
            title="Vorlage Projektanalyse bearbeiten"
          >
            <span className="inline-flex items-center justify-center text-accent flex-shrink-0"><Icon.FileText /></span>
            <span className="flex-1 whitespace-nowrap overflow-hidden text-ellipsis">Vorlage Analyse</span>
            <span className="inline-flex items-center justify-center text-text-tertiary opacity-0 group-hover:opacity-100 transition-opacity duration-150 flex-shrink-0"><Icon.Edit /></span>
          </button>

          <div className="p-3 flex-shrink-0">
            <input
              className="sidebar-search-input"
              placeholder="Projekte suchen…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <button
            className="mt-2 mb-1 mx-3 flex items-center gap-2 bg-bg-input border border-border text-text px-3 py-2.5 rounded-md text-[13px] font-medium transition-[background-color,border-color] duration-150 hover:bg-bg-hover hover:border-border-strong"
            onClick={() => onNewProject && onNewProject()}
          >
            <Icon.PlusBig />
            Neues Projekt
          </button>

          <div className="flex-1 overflow-y-auto px-2 pb-3">
            {visibleProjects.map((proj) => (
              <ProjectSection
                key={proj.id}
                proj={proj}
                activeChatId={activeChatId}
                setActiveChatId={setActiveChatId}
                onToggle={() => onToggleProject(proj.id)}
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
                isDragSource={draggingProjectId === proj.id}
                emptyChatIds={emptyChatIds}
              />
            ))}
            {visibleProjects.length === 0 && (
              <div className="chat-list-empty">Keine Projekte gefunden</div>
            )}
          </div>
        </>
      )}

      <div
        className={
          "relative border-t border-border mt-auto flex-shrink-0 " +
          (collapsed ? "py-2.5 px-0" : "py-2.5 px-3")
        }
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setUserMenuOpen((v) => !v);
          }}
          title={collapsed ? user.email : "Konto"}
          className={
            "w-full flex items-center gap-2.5 rounded-md transition-[background-color] duration-150 hover:bg-bg-hover " +
            (collapsed ? "justify-center py-1.5" : "px-2 py-1.5 text-left")
          }
        >
          <div className="avatar">{(user.email[0] || "A").toUpperCase()}</div>
          {!collapsed && (
            <div className="flex flex-col flex-1 min-w-0">
              <div className="text-[13px] font-medium text-text">{user.email.split("@")[0]}</div>
              <div className="text-[11px] text-text-tertiary whitespace-nowrap overflow-hidden text-ellipsis">{user.email}</div>
            </div>
          )}
          {!collapsed && (
            <span className="text-text-tertiary flex-shrink-0">
              <Icon.ChevronDownSm />
            </span>
          )}
        </button>
        {userMenuOpen && (
          <div
            className="absolute bottom-[calc(100%-4px)] left-3 right-3 z-30 bg-bg-elevated border border-border rounded-[8px] shadow-[0_12px_28px_rgba(0,0,0,.45),0_2px_6px_rgba(0,0,0,.3)] py-1"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="w-full flex items-center gap-2 px-3 py-2 text-[13px] text-text hover:bg-bg-hover transition-[background-color] duration-150 text-left"
              onClick={() => {
                setUserMenuOpen(false);
                onLogout();
              }}
            >
              <Icon.LogOut />
              <span>Abmelden</span>
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
