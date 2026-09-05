// The cache behind the transcript's speaker turns: a change to one segment
// must produce a new turn for that segment only, so the memoised cards for
// everything else keep their props.
import { describe, expect, it } from "vitest";

import { mergeBySpeaker, type TurnCache } from "@/lib/turns";
import type { Segment } from "@/types";

const seg = (id: string, speaker: string | null, text = id): Segment => ({
  segmentId: id, startedAt: Number(id.slice(1)), endedAt: Number(id.slice(1)) + 1,
  origText: text, origStatus: "final", transText: "", transStatus: "skipped", speaker,
});

describe("mergeBySpeaker with a cache", () => {
  it("hands back the same turn objects while their segments are unchanged", () => {
    const cache: TurnCache = new Map();
    const a = [seg("s1", "A"), seg("s2", "A"), seg("s3", "B"), seg("s4", null)];
    const first = mergeBySpeaker(a, cache);
    expect(first.map((t) => t.relatedIds)).toEqual([["s1", "s2"], ["s3"], ["s4"]]);

    // A partial updates s4 only: the first two turns are the very same objects.
    const b = [a[0], a[1], a[2], { ...a[3], origText: "partial…" }];
    const second = mergeBySpeaker(b, cache);
    expect(second[0]).toBe(first[0]);
    expect(second[1]).toBe(first[1]);
    expect(second[2]).not.toBe(first[2]);
    expect(second[2].segment.origText).toBe("partial…");
  });

  it("rebuilds a merged turn when any of its segments changed", () => {
    const cache: TurnCache = new Map();
    const a = [seg("s1", "A"), seg("s2", "A")];
    const first = mergeBySpeaker(a, cache);
    const second = mergeBySpeaker([a[0], { ...a[1], origText: "edited" }], cache);
    expect(second[0]).not.toBe(first[0]);
    expect(second[0].segment.origText).toBe("s1 edited");
  });

  it("keeps only the turns in use", () => {
    const cache: TurnCache = new Map();
    mergeBySpeaker([seg("s1", "A"), seg("s2", "B")], cache);
    mergeBySpeaker([seg("s1", "A")], cache);
    expect([...cache.keys()]).toEqual(["s1"]);
  });

  it("is the plain merge without a cache", () => {
    expect(mergeBySpeaker([seg("s1", "A"), seg("s2", "A")]).length).toBe(1);
  });
});
