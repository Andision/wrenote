import { useState } from "react";
import { motion } from "motion/react";
import {
  ChevronRight,
  Download,
  Folder,
  FolderPlus,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatRelativeTime } from "@/lib/colors";
import { recordingUrl } from "@/lib/recording";
import { confirmDialog } from "@/lib/confirm";
import { useSessionStore } from "@/store/sessionStore";
import type { SessionMeta } from "@/types";

const UNGROUPED = "__ungrouped__";

/**
 * Left rail. Always present (Claude/ChatGPT style): a thin icon rail when
 * collapsed, a full session panel when expanded. Sessions can be organised
 * into groups (folders); drag a session onto a group to file it, or onto
 * "Recent" to take it back out.
 */
export function Sidebar() {
  const open = useSessionStore((s) => s.sidebarOpen);
  const pastSessions = useSessionStore((s) => s.pastSessions);
  const groups = useSessionStore((s) => s.groups);
  const activeId = useSessionStore((s) => s.sessionId);
  const loadSession = useSessionStore((s) => s.loadSession);
  const deletePast = useSessionStore((s) => s.deletePastSession);
  const startNew = useSessionStore((s) => s.startNewSession);
  const connection = useSessionStore((s) => s.connection);
  const toggleSidebar = useSessionStore((s) => s.toggleSidebar);
  const toggleSettings = useSessionStore((s) => s.toggleSettings);
  const createGroup = useSessionStore((s) => s.createGroup);
  const renameGroup = useSessionStore((s) => s.renameGroup);
  const deleteGroup = useSessionStore((s) => s.deleteGroup);
  const moveSessionToGroup = useSessionStore((s) => s.moveSessionToGroup);

  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [editingGroup, setEditingGroup] = useState<string | null>(null);
  const [groupDraft, setGroupDraft] = useState("");
  const [dragOver, setDragOver] = useState<string | null>(null);

  // Past-session navigation is locked while a session is live, so the top-bar
  // transport controls can't operate on a frozen, unrelated view.
  const locked =
    connection === "recording" ||
    connection === "paused" ||
    connection === "connecting" ||
    connection === "stopping";

  const newSession = () => {
    if (!locked) startNew();
  };

  const ungrouped = pastSessions.filter((s) => !s.groupId);
  const membersOf = (gid: string) =>
    pastSessions.filter((s) => s.groupId === gid);

  const toggleCollapse = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const commitGroupRename = () => {
    const id = editingGroup;
    setEditingGroup(null);
    if (id && groupDraft.trim()) renameGroup(id, groupDraft.trim());
  };

  const dropHandlers = (target: string, groupId: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      if (!e.dataTransfer.types.includes("text/plain")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      if (dragOver !== target) setDragOver(target);
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
        setDragOver((d) => (d === target ? null : d));
      }
    },
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      const id = e.dataTransfer.getData("text/plain");
      if (id) moveSessionToGroup(id, groupId);
      setDragOver(null);
    },
  });

  const renderSession = (s: SessionMeta) => {
    const isActive = s.id === activeId;
    const date = new Date(s.createdAt);
    const dateStr = date.toLocaleDateString();
    const timeStr = date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    return (
      <div
        key={s.id}
        role="button"
        tabIndex={0}
        draggable={!locked}
        onDragStart={(e) => {
          e.dataTransfer.setData("text/plain", s.id);
          e.dataTransfer.effectAllowed = "move";
        }}
        onClick={() => {
          if (!locked) void loadSession(s.id);
        }}
        data-tip={
          locked && !isActive
            ? "Stop the current session before loading another"
            : undefined
        }
        className={`group flex w-full items-start gap-2 rounded-md px-2 py-2 text-left transition-colors ${
          isActive
            ? "bg-accent"
            : locked
              ? "cursor-not-allowed opacity-40"
              : "cursor-pointer hover:bg-accent/60"
        }`}
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-foreground">
            {s.title}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {dateStr} · {timeStr} · {formatRelativeTime(s.durationS)} ·{" "}
            {s.srcLang.toUpperCase()}→{s.tgtLang.toUpperCase()}
          </div>
        </div>
        <span className="invisible flex shrink-0 items-center gap-0.5 group-hover:visible">
          <a
            href={recordingUrl(s.id)}
            onClick={(e) => e.stopPropagation()}
            download={`${s.title || s.id}.wav`}
            data-tip="Download recording"
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <Download className="size-3.5" />
          </a>
          <span
            role="button"
            tabIndex={0}
            aria-label="Delete session"
            onClick={(e) => {
              e.stopPropagation();
              void confirmDialog({
                title: "Delete session?",
                description: `"${s.title}" and its recording will be permanently deleted.`,
                confirmLabel: "Delete",
                destructive: true,
              }).then((ok) => {
                if (ok) void deletePast(s.id);
              });
            }}
            className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="size-3.5" />
          </span>
        </span>
      </div>
    );
  };

  const renderGroup = (g: { id: string; name: string }) => {
    const members = membersOf(g.id);
    const isCollapsed = collapsed.has(g.id);
    const isEditing = editingGroup === g.id;
    return (
      <div
        key={g.id}
        {...dropHandlers(g.id, g.id)}
        className={`rounded-md transition-colors ${
          dragOver === g.id ? "bg-brand-500/10 ring-1 ring-brand-500/40" : ""
        }`}
      >
        <div className="group flex items-center gap-1 rounded-md px-1 py-1 hover:bg-accent/40">
          <button
            onClick={() => toggleCollapse(g.id)}
            className="flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:text-foreground"
          >
            <ChevronRight
              className={`size-3.5 transition-transform ${isCollapsed ? "" : "rotate-90"}`}
            />
          </button>
          <Folder className="size-3.5 shrink-0 text-muted-foreground" />
          {isEditing ? (
            <input
              autoFocus
              value={groupDraft}
              onChange={(e) => setGroupDraft(e.target.value)}
              onBlur={commitGroupRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitGroupRename();
                if (e.key === "Escape") setEditingGroup(null);
              }}
              className="h-5 min-w-0 flex-1 rounded border border-brand-500/30 bg-background px-1 text-[12px] font-semibold outline-none focus:ring-2 focus:ring-brand-500/30"
            />
          ) : (
            <span
              onDoubleClick={() => {
                setEditingGroup(g.id);
                setGroupDraft(g.name);
              }}
              className="min-w-0 flex-1 truncate text-[12px] font-semibold text-foreground"
              data-tip="Double-click to rename"
            >
              {g.name}
            </span>
          )}
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
            {members.length}
          </span>
          <span
            role="button"
            tabIndex={0}
            aria-label="Delete group"
            onClick={() => {
              void confirmDialog({
                title: "Delete group?",
                description: `"${g.name}" will be removed. Its sessions are kept and moved out of the group.`,
                confirmLabel: "Delete",
                destructive: true,
              }).then((ok) => {
                if (ok) void deleteGroup(g.id);
              });
            }}
            className="invisible shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive group-hover:visible"
          >
            <Trash2 className="size-3.5" />
          </span>
        </div>
        {!isCollapsed && (
          <div className="ml-3 space-y-0.5 border-l border-border pl-1">
            {members.length === 0 ? (
              <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
                Drag sessions here.
              </p>
            ) : (
              members.map(renderSession)
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <motion.aside
      initial={false}
      animate={{ width: open ? 288 : 56 }}
      transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
      className="relative z-20 flex shrink-0 flex-col overflow-hidden border-r bg-sidebar"
    >
      {open ? (
        <div className="flex h-full w-72 flex-col">
          {/* Header: wordmark + collapse */}
          <header className="flex h-16 shrink-0 items-center justify-between border-b px-4">
            <span className="text-[15px] font-semibold tracking-tight text-foreground">
              Wrenote
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => toggleSidebar(false)}
              data-tip="Collapse sidebar"
            >
              <PanelLeftClose className="size-4" />
            </Button>
          </header>

          {/* New session bar */}
          <div className="p-2">
            <button
              onClick={newSession}
              disabled={locked}
              data-tip={locked ? "Stop the current session first" : "New session"}
              className="flex w-full items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Plus className="size-4" />
              New session
            </button>
          </div>

          {locked && (
            <div className="px-3 pb-1 text-[11px] text-muted-foreground">
              Switching sessions is disabled while recording.
            </div>
          )}

          {/* Sessions + groups */}
          <ScrollArea className="flex-1">
            <div className="space-y-0.5 p-2">
              {/* New group action */}
              <button
                onClick={() => void createGroup()}
                className="mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                <FolderPlus className="size-4" />
                New group
              </button>

              {groups.map(renderGroup)}

              {/* Ungrouped — also a drop target so you can pull a session out. */}
              <div
                {...dropHandlers(UNGROUPED, null)}
                className={`mt-1 rounded-md transition-colors ${
                  dragOver === UNGROUPED
                    ? "bg-brand-500/10 ring-1 ring-brand-500/40"
                    : ""
                }`}
              >
                <div className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Recent
                </div>
                {pastSessions.length === 0 ? (
                  <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                    No sessions yet. Hit{" "}
                    <span className="font-medium">New session</span> to start.
                  </p>
                ) : ungrouped.length === 0 ? (
                  <p className="px-2 py-2 text-[11px] text-muted-foreground">
                    Everything's filed into groups.
                  </p>
                ) : (
                  ungrouped.map(renderSession)
                )}
              </div>
            </div>
          </ScrollArea>

          {/* Settings — height-matched to the status bar (h-10) so the bottom
              strip lines up across the whole window. */}
          <div className="flex h-10 shrink-0 items-center border-t px-2">
            <button
              onClick={() => toggleSettings()}
              className="flex h-7 w-full items-center gap-2 rounded-md px-2 text-[13px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <Settings className="size-4" />
              Settings
            </button>
          </div>
        </div>
      ) : (
        /* Collapsed rail — branding + new-session + settings, nothing else. */
        <div className="flex h-full w-14 flex-col items-center">
          <div className="flex flex-col items-center gap-1 pt-3">
            <div className="mb-2 flex size-8 items-center justify-center rounded-lg bg-foreground text-[13px] font-bold text-background">
              W
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-9"
              onClick={() => toggleSidebar(true)}
              data-tip="Expand sidebar"
              data-tip-side="right"
            >
              <PanelLeftOpen className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-9"
              onClick={newSession}
              disabled={locked}
              data-tip={locked ? "Stop the current session first" : "New session"}
              data-tip-side="right"
            >
              <Plus className="size-4" />
            </Button>
          </div>
          <div className="flex-1" />
          {/* Settings band — h-10 + border-t, mirrors the status bar so the
              bottom strip is continuous across rail and footer. */}
          <div className="flex h-10 w-full shrink-0 items-center justify-center border-t">
            <Button
              variant="ghost"
              size="icon"
              className="size-9"
              onClick={() => toggleSettings()}
              data-tip="Settings"
              data-tip-side="right"
            >
              <Settings className="size-4" />
            </Button>
          </div>
        </div>
      )}
    </motion.aside>
  );
}
