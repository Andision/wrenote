// The one thing worth asserting about a two-line helper: it sets *both*, which
// is the whole reason it exists rather than each site writing them out.
import { describe, expect, it } from "vitest";

import { iconTip } from "@/lib/tooltip";

describe("iconTip", () => {
  it("is the tooltip and the accessible name at once", () => {
    expect(iconTip("Start recording")).toEqual({
      "data-tip": "Start recording",
      "aria-label": "Start recording",
    });
  });
});
