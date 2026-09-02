import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { Check, Download, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatEta, subscribeJob } from "@/lib/jobs";
import {
  getModelStatus,
  startModelDownload,
  type ModelStatusItem,
} from "@/lib/models";

type Phase = "checking" | "needed" | "downloading" | "error";

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

/**
 * First-run gate. Renders nothing once the required models are present (the
 * common case after setup). When any are missing it takes over the screen with
 * a one-time download flow, streaming progress from the shared /jobs SSE.
 */
export function ModelGate() {
  const [phase, setPhase] = useState<Phase>("checking");
  const [models, setModels] = useState<ModelStatusItem[]>([]);
  const [done, setDone] = useState(false);
  const [fraction, setFraction] = useState(0);
  const [label, setLabel] = useState("");
  const [eta, setEta] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getModelStatus()
      .then((st) => {
        if (!alive) return;
        if (st.all_present) setDone(true);
        else {
          setModels(st.models);
          setPhase("needed");
        }
      })
      .catch((e) => {
        if (alive) {
          setError(String(e?.message ?? e));
          setPhase("error");
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  const beginDownload = () => {
    setPhase("downloading");
    setError("");
    setFraction(0);
    startModelDownload()
      .then((res) => {
        if (res.all_present || !res.job_id) {
          setDone(true);
          return;
        }
        subscribeJob(res.job_id, {
          onSnapshot: (snap) => {
            setFraction(snap.fraction);
            setEta(snap.eta_s);
            setLabel(snap.log[snap.log.length - 1] ?? snap.phase);
            if (snap.status === "done") setDone(true);
            if (snap.status === "error") {
              setError(snap.error ?? "download failed");
              setPhase("error");
            }
          },
          onError: () => {
            setError("connection to download stream lost");
            setPhase("error");
          },
        });
      })
      .catch((e) => {
        setError(String(e?.message ?? e));
        setPhase("error");
      });
  };

  if (done || phase === "checking") return null;

  const totalSize = models.reduce((a, m) => a + m.size, 0);
  const missing = models.filter((m) => !m.present);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/95 p-6 backdrop-blur-md"
    >
      <motion.div
        initial={{ opacity: 0, y: 10, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.22, 0.61, 0.36, 1] }}
        className="w-full max-w-md rounded-3xl border border-border/60 bg-card/80 p-8 shadow-xl"
      >
        <div className="flex size-14 items-center justify-center rounded-2xl bg-brand-500/15 ring-1 ring-inset ring-brand-500/25">
          <Download className="size-7 text-brand-600 dark:text-brand-400" />
        </div>

        <h1 className="mt-5 text-xl font-semibold tracking-tight text-foreground">
          Set up Wrenote
        </h1>
        <p className="mt-2 flex items-center gap-1.5 text-[13px] leading-relaxed text-muted-foreground">
          <ShieldCheck className="size-4 shrink-0 text-brand-500" />
          Runs fully on your device. One-time download of the speech &amp;
          translation models.
        </p>

        {/* Model checklist */}
        <ul className="mt-5 space-y-1.5">
          {models.map((m) => (
            <li
              key={m.filename}
              className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-[12px]"
            >
              <span className="truncate font-mono text-muted-foreground" title={m.filename}>
                {m.filename}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="tabular-nums text-muted-foreground/70">{gb(m.size)}</span>
                {m.present ? (
                  <Check className="size-4 text-emerald-500" />
                ) : (
                  <Download className="size-3.5 text-muted-foreground/50" />
                )}
              </span>
            </li>
          ))}
        </ul>

        {phase === "downloading" && (
          <div className="mt-6 space-y-2">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <motion.div
                className="h-full rounded-full bg-brand-500"
                animate={{ width: `${Math.round(fraction * 100)}%` }}
                transition={{ ease: "linear", duration: 0.2 }}
              />
            </div>
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3 animate-spin" />
                {label || "Starting…"}
              </span>
              <span className="tabular-nums">
                {Math.round(fraction * 100)}% · {formatEta(eta)}
              </span>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="mt-5 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}

        {(phase === "needed" || phase === "error") && (
          <Button onClick={beginDownload} className="mt-6 w-full" size="lg">
            {phase === "error" ? "Retry download" : `Download ${gb(totalSize)}`}
          </Button>
        )}

        {phase === "needed" && missing.length > 0 && (
          <p className="mt-3 text-center text-[11px] text-muted-foreground/60">
            Saved to ~/.wrenote/models · resumes if interrupted
          </p>
        )}
      </motion.div>
    </motion.div>
  );
}
