import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, FileDown } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  type ExportContent,
  type ExportFormat,
  downloadText,
  fetchExport,
} from "@/lib/export";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface ExportMenuProps {
  sessionId: string;
  title: string;
  /** Whether the session has any translated segments — gates the bilingual options. */
  hasTranslations: boolean;
}

// Labels are keys; the file extensions inside them are not translated.
const FORMATS: { fmt: ExportFormat; key: string }[] = [
  { fmt: "md", key: "export.format.md" },
  { fmt: "txt", key: "export.format.txt" },
  { fmt: "srt", key: "export.format.srt" },
  { fmt: "vtt", key: "export.format.vtt" },
];

/**
 * Export-transcript button + lightweight popover menu. Picks the content
 * (original / translation / both) then copies or downloads in a chosen format.
 * The backend renders the text; we just copy/save it.
 */
export function ExportMenu({ sessionId, title, hasTranslations }: ExportMenuProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<ExportContent>(hasTranslations ? "both" : "original");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("export.failed"));
    } finally {
      setBusy(false);
    }
  };

  const onCopy = () =>
    run(async () => {
      const text = await fetchExport(sessionId, "txt", content);
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      toast.success(t("export.copied"));
    });

  const onDownload = (fmt: ExportFormat) =>
    run(async () => {
      const text = await fetchExport(sessionId, fmt, content);
      downloadText(title || sessionId, fmt, text);
      setOpen(false);
    });

  const CONTENTS: { value: ExportContent; label: string; disabled?: boolean }[] = [
    { value: "original", label: t("export.original") },
    { value: "translation", label: t("export.translation"), disabled: !hasTranslations },
    { value: "both", label: t("export.both"), disabled: !hasTranslations },
  ];

  return (
    <div className="relative" ref={ref}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((v) => !v)}
        data-tip={t("export.tooltip")}
        aria-haspopup="menu"
        aria-expanded={open}
        className={open ? "size-9 bg-accent text-foreground" : "size-9"}
      >
        <FileDown className="size-4" />
      </Button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-1 w-56 rounded-lg border border-border bg-popover p-1.5 text-popover-foreground shadow-lg"
        >
          {/* Content selector */}
          <div className="px-1.5 pb-1.5 pt-1">
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Include
            </div>
            <div className="flex gap-1">
              {CONTENTS.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  disabled={c.disabled}
                  onClick={() => setContent(c.value)}
                  className={cn(
                    "flex-1 rounded-md px-2 py-1 text-[12px] transition-colors",
                    content === c.value
                      ? "bg-brand-600 text-white"
                      : "bg-accent/50 text-foreground hover:bg-accent",
                    c.disabled && "cursor-not-allowed opacity-40",
                  )}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>

          <div className="my-1 h-px bg-border" />

          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={onCopy}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-accent disabled:opacity-50"
          >
            {copied ? <Check className="size-4 text-green-600" /> : <Copy className="size-4" />}
            Copy text
          </button>

          {FORMATS.map((f) => (
            <button
              key={f.fmt}
              type="button"
              role="menuitem"
              disabled={busy}
              onClick={() => onDownload(f.fmt)}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-accent disabled:opacity-50"
            >
              <Download className="size-4 text-muted-foreground" />
              {t(f.key)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
