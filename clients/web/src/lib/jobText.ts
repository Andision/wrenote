// One place that turns a tracked job into words.
//
// Refine jobs are tracked with the bare session title, because the engine
// starts them itself and the client learns about them from the session list —
// outside React, where there is no `t()`. So the wording is added at render,
// and it is added in two places now (the toast and the list), which is why it
// lives here rather than in either.
import type { TFunction } from "@/i18n";
import type { JobKind, TrackedJob } from "@/store/jobsStore";

/** Message key per kind for a job whose label is only the session title. */
const TITLE_ONLY: Partial<Record<JobKind, string>> = {
  refine: "topbar.refine.jobLabel",
};

export function jobLabel(t: TFunction, job: { kind: JobKind; label: string }): string {
  const key = TITLE_ONLY[job.kind];
  return key ? t(key, { title: job.label }) : job.label;
}

/** What the job is doing right now, in one line. */
export function jobStatusLine(t: TFunction, job: TrackedJob): string {
  const snap = job.snapshot;
  if (snap?.status === "error") return snap.error || t("progress.failed");
  if (snap?.status === "done") return t("progress.complete");
  return snap?.phase || t("progress.starting");
}
