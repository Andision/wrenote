// Settings → About: which Wrenote this is, and what it is standing on.
//
// Wrenote is AGPL, and it ships other people's work — speech models, an
// inference runtime, a UI toolkit. Saying so is the licence's own
// requirement and, for a local-first app whose pitch is that you can see
// what it does, the honest thing anyway.
//
// Nothing here is written from memory: the Python licences come from the
// installed distributions, the rest from a curated file where an entry may
// give only a link. A licence someone half-remembered is worse than a link.
import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";

import { Label } from "@/components/ui/label";
import { useT } from "@/i18n";
import { getAbout, type About, type Component } from "@/lib/about";
import { openExternal } from "@/lib/update";
import { UpdatePanel } from "@/components/UpdatePanel";

export function AboutPanel() {
  const t = useT();
  const [about, setAbout] = useState<About | null>(null);

  useEffect(() => {
    let alive = true;
    getAbout()
      .then((a) => {
        if (alive) setAbout(a);
      })
      .catch(() => {
        /* the version and update state below don't depend on it */
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="space-y-6">
      <UpdatePanel />

      {about && (
        <section className="space-y-1 border-t border-border pt-5">
          <Label className="text-xs text-foreground">{t("about.licenseTitle")}</Label>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("about.licenseBody", { license: about.license })}
          </p>
          <button
            type="button"
            onClick={() => openExternal(about.source)}
            className="inline-flex items-center gap-1 text-[11px] text-brand-600 underline-offset-2 hover:underline dark:text-brand-400"
          >
            {about.source}
            <ExternalLink className="size-3" />
          </button>
        </section>
      )}

      {about && (
        <>
          <Group title={t("about.models")} hint={t("about.modelsHint")} items={about.models} />
          <Group title={t("about.native")} items={about.native} />
          <Group title={t("about.web")} items={about.web} />
          <Group title={t("about.python")} items={about.python} />
        </>
      )}
    </div>
  );
}

function Group({
  title,
  hint,
  items,
}: {
  title: string;
  hint?: string;
  items: Component[];
}) {
  const t = useT();
  if (items.length === 0) return null;
  return (
    <section className="space-y-2">
      <Label className="text-xs text-foreground">{title}</Label>
      {hint && <p className="text-[11px] leading-relaxed text-muted-foreground">{hint}</p>}
      <ul className="divide-y divide-border/50 rounded-lg border border-border/50 bg-background/40">
        {items.map((c) => (
          <li key={`${c.name}-${c.version ?? ""}`} className="flex items-baseline gap-2 px-3 py-1.5">
            <span className="min-w-0 flex-1 truncate text-[12px] text-foreground">
              {c.name}
              {c.version && (
                <span className="ml-1.5 tabular-nums text-[11px] text-muted-foreground">
                  {c.version}
                </span>
              )}
            </span>
            {c.license ? (
              <span className="shrink-0 text-[11px] text-muted-foreground">{c.license}</span>
            ) : (
              c.url && (
                <span className="shrink-0 text-[11px] text-muted-foreground/70">
                  {t("about.seeUpstream")}
                </span>
              )
            )}
            {c.url && (
              <button
                type="button"
                onClick={() => openExternal(c.url!)}
                aria-label={t("about.open", { name: c.name })}
                data-tip={c.url}
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <ExternalLink className="size-3" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
