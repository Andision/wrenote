import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  FileAudio,
  FileVideo,
  UploadCloud,
  X,
} from "lucide-react";

import {
  LanguageSelect,
  SOURCE_LANGUAGES,
  TARGET_LANGUAGES,
} from "@/components/LanguageSelect";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { startUpload } from "@/lib/upload";
import { useJobsStore } from "@/store/jobsStore";
import { useSessionStore } from "@/store/sessionStore";

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Modal for "import a media file" flow. Drop or pick N files, reorder
 * with arrow buttons (drag-and-drop would need a lib; not worth it for
 * this scope), set lang + translate toggle, hit Transcribe.
 * Streams progress, then auto-loads the new session and closes.
 */
export function UploadDialog({ open, onClose }: UploadDialogProps) {
  const settings = useSessionStore((s) => s.settings);
  const updateSettings = useSessionStore((s) => s.updateSettings);
  const trackJob = useJobsStore((s) => s.track);

  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset state on each fresh open.
  useEffect(() => {
    if (open) {
      setFiles([]);
      setTitle("");
      setSubmitting(false);
      setErr(null);
    }
  }, [open]);

  const addFiles = useCallback((picked: FileList | File[]) => {
    const list = Array.from(picked);
    setFiles((prev) => [...prev, ...list]);
  }, []);

  const move = (idx: number, delta: -1 | 1) => {
    setFiles((prev) => {
      const j = idx + delta;
      if (j < 0 || j >= prev.length) return prev;
      const next = prev.slice();
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  };

  const remove = (idx: number) =>
    setFiles((prev) => prev.filter((_, i) => i !== idx));

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer?.files) addFiles(e.dataTransfer.files);
  };

  const canStart = files.length > 0 && !submitting;

  const start = async () => {
    if (!canStart) return;
    setSubmitting(true);
    setErr(null);
    const finalTitle = title.trim() || defaultTitleFromFiles(files);
    try {
      const { jobId, sessionId } = await startUpload({
        files,
        title: finalTitle,
        srcLang: settings.srcLang,
        tgtLang: settings.tgtLang,
        translate: settings.translateEnabled,
      });
      // Register with the floating progress overlay. The kind-aware
      // onDone lives inside jobsStore so a refresh can reconstruct it.
      trackJob({
        jobId,
        label: `Transcribe: ${finalTitle}`,
        kind: "upload",
        sessionId,
      });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="upload-backdrop"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          onClick={(e) => {
            if (e.target === e.currentTarget && !submitting) onClose();
          }}
        >
          <motion.div
            key="upload-panel"
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.97 }}
            transition={{ duration: 0.2, ease: [0.22, 0.61, 0.36, 1] }}
            className="max-h-[88vh] w-[560px] max-w-[92vw] overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
          >
            <header className="flex items-center justify-between border-b px-5 py-3">
              <div className="flex items-center gap-2">
                <UploadCloud className="size-4 text-brand-600 dark:text-brand-400" />
                <h2 className="text-sm font-semibold">Transcribe from file</h2>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-7"
                onClick={onClose}
                disabled={submitting}
                data-tip="Close"
              >
                <X className="size-3.5" />
              </Button>
            </header>

            <div className="space-y-4 overflow-y-auto px-5 py-4">
              {/* Drop zone */}
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={onDrop}
                onClick={() => inputRef.current?.click()}
                className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-border bg-muted/30 px-6 py-8 text-center transition-colors hover:border-brand-500/40 hover:bg-brand-500/5"
              >
                <UploadCloud className="size-7 text-muted-foreground" />
                <div className="text-sm font-medium text-foreground">
                  Drop files or click to choose
                </div>
                <div className="text-[12px] text-muted-foreground">
                  Audio or video · any format ffmpeg supports · multiple files
                  will be concatenated in the listed order
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  multiple
                  accept="audio/*,video/*"
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files) addFiles(e.target.files);
                    e.target.value = ""; // allow re-selecting the same file
                  }}
                />
              </div>

              {/* File list */}
              {files.length > 0 && (
                <ul className="space-y-1.5">
                  {files.map((f, i) => (
                    <li
                      key={`${f.name}-${i}`}
                      className="flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 px-2.5 py-1.5"
                    >
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      {f.type.startsWith("video/") ? (
                        <FileVideo className="size-3.5 text-muted-foreground" />
                      ) : (
                        <FileAudio className="size-3.5 text-muted-foreground" />
                      )}
                      <span className="min-w-0 flex-1 truncate text-[13px]">
                        {f.name}
                      </span>
                      <span className="font-mono text-[10.5px] text-muted-foreground">
                        {formatSize(f.size)}
                      </span>
                      <button
                        onClick={() => move(i, -1)}
                        disabled={i === 0 || submitting}
                        className="rounded p-1 text-muted-foreground hover:bg-accent disabled:opacity-30"
                        data-tip="Move up"
                      >
                        <ArrowUp className="size-3" />
                      </button>
                      <button
                        onClick={() => move(i, 1)}
                        disabled={i === files.length - 1 || submitting}
                        className="rounded p-1 text-muted-foreground hover:bg-accent disabled:opacity-30"
                        data-tip="Move down"
                      >
                        <ArrowDown className="size-3" />
                      </button>
                      <button
                        onClick={() => remove(i)}
                        disabled={submitting}
                        className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30"
                        data-tip="Remove"
                      >
                        <X className="size-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {/* Title */}
              <div className="space-y-1.5">
                <label className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Title (optional)
                </label>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={defaultTitleFromFiles(files)}
                  disabled={submitting}
                  className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
                />
              </div>

              {/* Lang strip + translate toggle (mirrors PreFlight) */}
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border/60 bg-card/40 p-3">
                <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {settings.translateEnabled ? "From" : "Language"}
                </span>
                <LanguageSelect
                  value={settings.srcLang}
                  options={SOURCE_LANGUAGES}
                  onChange={(v) => updateSettings({ srcLang: v })}
                  disabled={submitting}
                  ariaLabel="Source language"
                />
                {settings.translateEnabled && (
                  <>
                    <ArrowRight className="size-4 text-muted-foreground/60" />
                    <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                      to
                    </span>
                    <LanguageSelect
                      value={settings.tgtLang}
                      options={TARGET_LANGUAGES}
                      onChange={(v) => updateSettings({ tgtLang: v })}
                      disabled={submitting}
                      ariaLabel="Target language"
                    />
                  </>
                )}
                <div className="ml-auto flex items-center gap-2 text-[12px] text-muted-foreground">
                  <Switch
                    checked={settings.translateEnabled}
                    onCheckedChange={(v) =>
                      updateSettings({ translateEnabled: v })
                    }
                    disabled={submitting}
                  />
                  <span>Translate</span>
                </div>
              </div>

              {err && (
                <p className="text-[12px] text-destructive">⚠ {err}</p>
              )}
            </div>

            <footer className="flex items-center justify-between gap-2 border-t bg-background/40 px-5 py-3">
              <span className="text-[11px] text-muted-foreground">
                Processing continues in the background — you can close this.
              </span>
              <div className="flex items-center gap-2">
                <Button variant="ghost" onClick={onClose} disabled={submitting}>
                  Cancel
                </Button>
                <Button
                  onClick={() => void start()}
                  disabled={!canStart}
                  className="gap-1.5 bg-brand-600 text-white hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-600"
                >
                  Transcribe{files.length > 0 ? ` (${files.length})` : ""}
                </Button>
              </div>
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function defaultTitleFromFiles(files: File[]): string {
  if (files.length === 0) return "Upload";
  const base = files[0].name.replace(/\.[^.]+$/, "");
  return files.length === 1 ? base : `${base} + ${files.length - 1} more`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
