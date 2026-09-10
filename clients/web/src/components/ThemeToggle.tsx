import { AnimatePresence, motion } from "motion/react";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

const ORDER = ["light", "dark", "system"] as const;
type Mode = (typeof ORDER)[number];

/**
 * Cycles Light → Dark → System (follow OS). next-themes seeds `theme` from
 * localStorage in a state initialiser, so the first client render already
 * has the right value — no mount gate needed in a client-only app. The icon
 * swap is a small motion fade.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const t = useT();
  const current: Mode = (ORDER as readonly string[]).includes(theme ?? "")
    ? (theme as Mode)
    : "system";
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const Icon = current === "light" ? Sun : current === "dark" ? Moon : Monitor;

  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-9"
      title={t("theme.toggle", {
        current: t(`theme.${current}`),
        next: t(`theme.${next}`),
      })}
      onClick={() => setTheme(next)}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={current}
          initial={{ opacity: 0, rotate: -30, scale: 0.7 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 30, scale: 0.7 }}
          transition={{ duration: 0.18 }}
          className="inline-flex"
        >
          <Icon className="size-4" />
        </motion.span>
      </AnimatePresence>
    </Button>
  );
}
