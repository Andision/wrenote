// Speaker turns: adjacent same-speaker segments shown as one card.
//
// The transcript renders these, not raw segments, and it re-derives them on
// every store change — a partial every ~800 ms in a live session. With a
// cache the derivation hands back the same turn object whenever the turn's
// input segments are the same objects as last time, so a memoised card sees
// unchanged props and does not render. On a two-hour meeting that is the
// difference between one card rendering per partial and fifteen hundred.
import type { Segment } from "@/types";

export interface SpeakerTurn {
  /** Synthetic merged segment for rendering. segmentId = first sub-segment id. */
  segment: Segment;
  /** Original segment IDs covered by this turn, in order. */
  relatedIds: string[];
}

/**
 * Build "speaker turns" — adjacent same-speaker segments collapse into one
 * synthetic merged segment for display. Pre-diarize content and any
 * segments labeled "unknown" fall through one-card-per-segment (no merge).
 */
function mergeOnce(ordered: Segment[]): SpeakerTurn[] {
  const out: SpeakerTurn[] = [];
  for (const seg of ordered) {
    const last = out[out.length - 1];
    const canMerge =
      last !== undefined &&
      seg.speaker != null &&
      seg.speaker !== "unknown" &&
      seg.speaker === last.segment.speaker;
    if (canMerge) {
      const prev = last.segment;
      const joinSep = (a: string, b: string) => (a && b ? `${a} ${b}` : a + b);
      // Don't let a single partial in the middle of an otherwise-final
      // turn mark the whole bubble as partial.
      const origStatus =
        prev.origStatus === "partial" || seg.origStatus === "partial"
          ? "partial"
          : "final";
      const transStatus: Segment["transStatus"] =
        prev.transStatus === "skipped" && seg.transStatus === "skipped"
          ? "skipped"
          : prev.transStatus === "partial" || seg.transStatus === "partial"
            ? "partial"
            : "final";
      const merged: Segment = {
        ...prev,
        endedAt: Math.max(prev.endedAt, seg.endedAt),
        origText: joinSep(prev.origText, seg.origText),
        origStatus,
        transText: joinSep(prev.transText, seg.transText),
        transStatus,
      };
      out[out.length - 1] = {
        segment: merged,
        relatedIds: [...last.relatedIds, seg.segmentId],
      };
    } else {
      out.push({ segment: seg, relatedIds: [seg.segmentId] });
    }
  }
  return out;
}

/** Previous turns by their id list, with the segments they were built from. */
export type TurnCache = Map<string, { inputs: Segment[]; turn: SpeakerTurn }>;

/**
 * Build the turns for `ordered`, reusing any turn from `cache` whose input
 * segments are identical (by reference) to last time. The cache is pruned
 * to the turns in use, so it never outgrows the transcript.
 */
export function mergeBySpeaker(ordered: Segment[], cache?: TurnCache): SpeakerTurn[] {
  const fresh = mergeOnce(ordered);
  if (!cache) return fresh;
  const byId = new Map<string, Segment>();
  for (const s of ordered) byId.set(s.segmentId, s);
  const next: TurnCache = new Map();
  const out: SpeakerTurn[] = [];
  for (const turn of fresh) {
    const key = turn.relatedIds.join(" ");
    const inputs = turn.relatedIds.map((id) => byId.get(id)!);
    const prev = cache.get(key);
    const reuse =
      prev !== undefined &&
      prev.inputs.length === inputs.length &&
      prev.inputs.every((s, i) => s === inputs[i]);
    const kept = reuse ? prev.turn : turn;
    next.set(key, { inputs, turn: kept });
    out.push(kept);
  }
  cache.clear();
  for (const [k, v] of next) cache.set(k, v);
  return out;
}
