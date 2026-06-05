import { useEffect, useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { type GlossaryEntry, getGlossary, saveGlossary } from "@/lib/glossary";

const EMPTY: GlossaryEntry = { term: "", translation: "", note: "" };

/**
 * Global glossary editor (Settings → Glossary). Names / jargon / proper nouns:
 * the term biases Whisper toward that spelling, and `term → translation` keeps
 * the translation consistent. Persisted on blur / add / remove.
 */
export function GlossaryEditor() {
  const [rows, setRows] = useState<GlossaryEntry[]>([]);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  useEffect(() => {
    void getGlossary().then(setRows);
  }, []);

  // Persist a concrete list (drops blank terms). Fire-and-forget; we keep the
  // local rows so an in-flight save never clobbers what the user is typing.
  const persist = (next: GlossaryEntry[]) =>
    void saveGlossary(next.filter((r) => r.term.trim()));

  const update = (i: number, patch: Partial<GlossaryEntry>) =>
    setRows((r) => r.map((row, j) => (j === i ? { ...row, ...patch } : row)));

  const addRow = () => setRows((r) => [...r, { ...EMPTY }]);

  const removeRow = (i: number) =>
    setRows((r) => {
      const next = r.filter((_, j) => j !== i);
      persist(next);
      return next;
    });

  const inputCls =
    "min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-[13px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-2 focus:ring-brand-500/30";

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-xs font-semibold text-foreground">Glossary · custom vocabulary</h3>
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          Names, jargon, and proper nouns. The term biases speech recognition toward that
          spelling; the optional translation keeps it consistent across the transcript.
        </p>
      </div>

      <div className="space-y-2">
        {rows.map((row, i) => (
          <div key={row.id ?? `row-${i}`} className="flex items-center gap-2">
            <input
              value={row.term}
              placeholder="Term (e.g. Kubernetes, 张伟)"
              onChange={(e) => update(i, { term: e.target.value })}
              onBlur={() => persist(rowsRef.current)}
              className={inputCls}
            />
            <span className="text-muted-foreground/50">→</span>
            <input
              value={row.translation}
              placeholder="Translation (optional)"
              onChange={(e) => update(i, { translation: e.target.value })}
              onBlur={() => persist(rowsRef.current)}
              className={inputCls}
            />
            <button
              type="button"
              onClick={() => removeRow(i)}
              data-tip="Remove"
              aria-label="Remove term"
              className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        {rows.length === 0 && (
          <p className="rounded-lg border border-dashed border-border/60 px-3 py-6 text-center text-[12px] text-muted-foreground">
            No terms yet. Add names or jargon your meetings use.
          </p>
        )}
      </div>

      <button
        type="button"
        onClick={addRow}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1.5 text-[13px] text-foreground hover:bg-accent"
      >
        <Plus className="size-3.5" />
        Add term
      </button>
    </div>
  );
}
