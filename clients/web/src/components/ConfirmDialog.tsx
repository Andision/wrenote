import { useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { useConfirmStore } from "@/lib/confirm";

/**
 * App-wide confirm modal driven by the confirm store. Renders a blurred
 * backdrop with a centered dialog and resolves the pending confirmDialog()
 * promise on action. Esc / backdrop click = cancel, Enter = confirm.
 */
export function ConfirmDialog() {
  const open = useConfirmStore((s) => s.open);
  const options = useConfirmStore((s) => s.options);
  const respond = useConfirmStore((s) => s.respond);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") respond(false);
      if (e.key === "Enter") respond(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, respond]);

  return (
    <AnimatePresence>
      {open && options && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
          <motion.div
            className="absolute inset-0 bg-background/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={() => respond(false)}
          />
          <motion.div
            role="alertdialog"
            aria-modal="true"
            className="relative z-10 w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-2xl"
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
          >
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">
              {options.title}
            </h2>
            {options.description && (
              <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
                {options.description}
              </p>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => respond(false)}>
                {options.cancelLabel ?? "Cancel"}
              </Button>
              <Button
                size="sm"
                autoFocus
                onClick={() => respond(true)}
                className={
                  options.destructive
                    ? "bg-destructive text-white hover:bg-destructive/90"
                    : "bg-brand-600 text-white hover:bg-brand-700"
                }
              >
                {options.confirmLabel ?? "Confirm"}
              </Button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
