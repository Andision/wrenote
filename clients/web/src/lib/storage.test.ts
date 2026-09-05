// The engine's session row → the client's SessionMeta. The lifecycle fields
// are new and optional on the wire; an engine that doesn't send them (or sends
// a status this client doesn't know) must read as a finished session, never
// as one stuck "processing".
import { describe, expect, it } from "vitest";

import { toMeta, toStatus } from "@/lib/storage";

const row = {
  id: "s1",
  title: "T",
  created_at: "2026-01-01T00:00:00Z",
  src_lang: "en",
  tgt_lang: "zh",
  duration_s: 12,
};

describe("toStatus", () => {
  it("passes known statuses through and defaults the rest to ready", () => {
    expect(toStatus("processing")).toBe("processing");
    expect(toStatus("failed")).toBe("failed");
    expect(toStatus(undefined)).toBe("ready");
    expect(toStatus("archived")).toBe("ready");
  });
});

describe("toMeta", () => {
  it("carries the lifecycle fields", () => {
    expect(
      toMeta({ ...row, status: "processing", job_id: "j1", refined_at: null, status_detail: null }),
    ).toMatchObject({ status: "processing", jobId: "j1", refinedAt: null, statusDetail: null });
    expect(toMeta({ ...row, status: "failed", status_detail: "interrupted" })).toMatchObject({
      status: "failed",
      statusDetail: "interrupted",
      jobId: null,
    });
  });

  it("reads an older engine's row as a finished session", () => {
    expect(toMeta(row)).toMatchObject({ status: "ready", jobId: null, refinedAt: null });
  });
});
