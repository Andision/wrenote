// Highlighting the query inside a hit: every occurrence, case-insensitively,
// with the original text's own casing kept.
import { describe, expect, it } from "vitest";

import { highlightRuns } from "@/lib/search";

describe("highlightRuns", () => {
  it("marks every occurrence and keeps the rest", () => {
    expect(highlightRuns("Budget, budget, BUDGET.", "budget")).toEqual([
      { text: "Budget", hit: true },
      { text: ", ", hit: false },
      { text: "budget", hit: true },
      { text: ", ", hit: false },
      { text: "BUDGET", hit: true },
      { text: ".", hit: false },
    ]);
  });

  it("works on Chinese and on a query with no match", () => {
    expect(highlightRuns("我们讨论了预算问题", "预算")).toEqual([
      { text: "我们讨论了", hit: false },
      { text: "预算", hit: true },
      { text: "问题", hit: false },
    ]);
    expect(highlightRuns("nothing here", "zzz")).toEqual([{ text: "nothing here", hit: false }]);
    expect(highlightRuns("x", "")).toEqual([{ text: "x", hit: false }]);
  });
});
