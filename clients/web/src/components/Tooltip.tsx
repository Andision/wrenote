import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

/**
 * One global tooltip for the whole app, in the style of the Claude / ChatGPT
 * web apps: a small dark bubble (black-on-white in light mode via the
 * foreground/background tokens) that fades in after a short hover delay.
 *
 * Usage: put `data-tip="…"` on any element. A single delegated listener
 * handles every trigger, so timing, styling and positioning stay identical
 * everywhere — unlike the native `title=` tooltip, which we no longer use.
 */

type Placement = "top" | "bottom" | "left" | "right";

interface TipState {
  text: string;
  x: number; // viewport anchor x
  y: number; // viewport anchor y
  placement: Placement;
}

// translate() per placement so the bubble's correct edge meets the anchor.
const TRANSFORM: Record<Placement, string> = {
  top: "translate(-50%, -100%)",
  bottom: "translate(-50%, 0)",
  left: "translate(-100%, -50%)",
  right: "translate(0, -50%)",
};

const SHOW_DELAY = 350; // ms hover before showing
const EDGE = 8; // viewport padding so the bubble never touches the edge

export function TooltipLayer() {
  const [tip, setTip] = useState<TipState | null>(null);
  const showTimer = useRef<number | null>(null);
  const targetRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const clearShow = () => {
      if (showTimer.current) {
        window.clearTimeout(showTimer.current);
        showTimer.current = null;
      }
    };
    const hide = () => {
      clearShow();
      targetRef.current = null;
      setTip(null);
    };

    const place = (el: HTMLElement, text: string) => {
      const r = el.getBoundingClientRect();
      // An explicit side wins (e.g. the collapsed sidebar rail asks for
      // "right" so its bubbles don't run off the left edge); otherwise prefer
      // above and flip below when there isn't room near the top.
      const hint = el.getAttribute("data-tip-side") as Placement | null;
      const placement: Placement = hint ?? (r.top > 48 ? "top" : "bottom");
      let x: number;
      let y: number;
      if (placement === "right") {
        x = r.right + 6;
        y = r.top + r.height / 2;
      } else if (placement === "left") {
        x = r.left - 6;
        y = r.top + r.height / 2;
      } else {
        x = Math.min(
          window.innerWidth - EDGE,
          Math.max(EDGE, r.left + r.width / 2),
        );
        y = placement === "top" ? r.top - 6 : r.bottom + 6;
      }
      setTip({ text, x, y, placement });
    };

    const onOver = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      const el = t?.closest?.("[data-tip]") as HTMLElement | null;
      if (!el) return;
      const text = el.getAttribute("data-tip");
      if (!text) return;
      if (targetRef.current === el) return; // same trigger, keep current state
      targetRef.current = el;
      clearShow();
      setTip(null);
      showTimer.current = window.setTimeout(() => place(el, text), SHOW_DELAY);
    };

    const onOut = (e: MouseEvent) => {
      const el = targetRef.current;
      if (!el) return;
      const related = e.relatedTarget as Node | null;
      if (related && el.contains(related)) return; // moved within the trigger
      hide();
    };

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };

    document.addEventListener("mouseover", onOver, true);
    document.addEventListener("mouseout", onOut, true);
    document.addEventListener("scroll", hide, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", hide);
    return () => {
      document.removeEventListener("mouseover", onOver, true);
      document.removeEventListener("mouseout", onOut, true);
      document.removeEventListener("scroll", hide, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", hide);
      clearShow();
    };
  }, []);

  return (
    <AnimatePresence>
      {tip && (
        <motion.div
          key="tooltip"
          // Only opacity is animated so motion never writes `transform` —
          // that keeps our manual centering/placement transform intact.
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.12, ease: "easeOut" }}
          style={{
            position: "fixed",
            left: tip.x,
            top: tip.y,
            transform: TRANSFORM[tip.placement],
            zIndex: 9999,
          }}
          className="pointer-events-none max-w-xs rounded-md bg-foreground px-2.5 py-1.5 text-center text-[11.5px] font-medium leading-snug text-background shadow-md"
        >
          {tip.text}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
