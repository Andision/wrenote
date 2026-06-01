// Kick off an upload job. Returns {jobId, sessionId} immediately;
// progress is streamed separately via subscribeJob (handled by jobsStore).

// Same-origin: the SPA is served by the backend, so talk to our own origin
// (port included). Vite dev proxies these paths to the backend — see vite.config.ts.
const BASE =
  typeof window !== "undefined" ? window.location.origin : "http://localhost:8000";

export interface UploadParams {
  files: File[];
  title: string;
  srcLang: string;
  tgtLang: string;
  translate: boolean;
}

export interface UploadStarted {
  jobId: string;
  sessionId: string;
}

export async function startUpload(params: UploadParams): Promise<UploadStarted> {
  const fd = new FormData();
  for (const f of params.files) fd.append("files", f, f.name);
  fd.append("title", params.title);
  fd.append("src_lang", params.srcLang);
  fd.append("tgt_lang", params.tgtLang);
  fd.append("translate", String(params.translate));

  const res = await fetch(`${BASE}/sessions/upload`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Upload failed (${res.status}): ${text}`);
  }
  const json = (await res.json()) as { job_id: string; session_id: string };
  return { jobId: json.job_id, sessionId: json.session_id };
}
