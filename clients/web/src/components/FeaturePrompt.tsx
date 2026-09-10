// What a switched-off feature says when someone reaches for it.
//
// The alternative was hiding the buttons for features the user declined,
// which makes them undiscoverable: nothing in the app would ever mention
// that it can write minutes. So the buttons stay, and this is what they do
// instead — name the feature, say what it costs, and offer the way back to
// the setup flow with that feature already switched on. Ignoring closes it;
// clicking the button again asks again, because clicking it is the ask.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { SLOT_FOR_FEATURE, type OptionalFeature, getModelStatus } from "@/lib/models";
import { useSessionStore } from "@/store/sessionStore";

export function FeaturePrompt() {
  const feature = useSessionStore((s) => s.featurePrompt);
  const dismiss = useSessionStore((s) => s.dismissFeaturePrompt);

  useEffect(() => {
    if (!feature) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [feature, dismiss]);

  return (
    <AnimatePresence>
      {feature && <Body key={feature} feature={feature} />}
    </AnimatePresence>
  );
}

/** Keyed on the feature, so asking about a different one starts from a blank
 *  size rather than briefly showing the previous feature's download. */
function Body({ feature }: { feature: OptionalFeature }) {
  const dismiss = useSessionStore((s) => s.dismissFeaturePrompt);
  const openSetup = useSessionStore((s) => s.openSetup);
  const t = useT();
  // The download size, read from the catalogue rather than written into a
  // message: it changes with the model the user has chosen for that slot.
  const [sizeMb, setSizeMb] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    void getModelStatus()
      .then((st) => {
        if (!alive) return;
        const kind = st.options.find((k) => k.kind === SLOT_FOR_FEATURE[feature]);
        const pick =
          kind?.options.find((o) => o.selected) ??
          kind?.options.find((o) => o.recommended) ??
          kind?.options[0];
        setSizeMb(pick && !pick.installed ? pick.size_mb : null);
      })
      .catch(() => {
        /* the size is a nicety; the offer stands without it */
      });
    return () => {
      alive = false;
    };
  }, [feature]);

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
      <motion.div
        className="absolute inset-0 bg-background/60 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.15 }}
        onClick={dismiss}
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
        <div className="flex size-9 items-center justify-center rounded-xl bg-brand-500/15 ring-1 ring-inset ring-brand-500/25">
          <Download className="size-4 text-brand-600 dark:text-brand-400" />
        </div>
        <h2 className="mt-3 text-[15px] font-semibold tracking-tight text-foreground">
          {t("feature.offTitle", { feature: t(`setup.feature.${feature}`) })}
        </h2>
        <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
          {t(`setup.feature.${feature}Hint`)}{" "}
          {sizeMb == null
            ? t("feature.offBody")
            : t("feature.offBodySized", { mb: sizeMb })}
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={dismiss}>
            {t("feature.ignore")}
          </Button>
          <Button size="sm" onClick={() => openSetup(feature)}>
            {t("feature.enable")}
          </Button>
        </div>
      </motion.div>
    </div>
  );
}
