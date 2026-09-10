// The tap gesture and what it persists.
//
// Worth holding because the alternative is a setting, and a setting for the
// developer tools is a setting every user has to read past. Five taps is the
// contract: not reachable by a stray double-click, and it survives a restart
// because the person who turned it on is mid-task.
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { TAPS_BEFORE_HINT, TAPS_REQUIRED, setDevMode, useDevMode } from "@/lib/devMode";

describe("developer mode", () => {
  beforeEach(() => {
    setDevMode(false);
  });

  it("takes more taps than any accidental click run", () => {
    expect(TAPS_REQUIRED).toBeGreaterThanOrEqual(5);
    expect(TAPS_BEFORE_HINT).toBeLessThan(TAPS_REQUIRED);
  });

  it("persists, so it survives a restart mid-task", () => {
    setDevMode(true);
    expect(localStorage.getItem("wrenote.devMode")).toBe("1");
    setDevMode(false);
    expect(localStorage.getItem("wrenote.devMode")).toBe("0");
  });

  it("re-renders everything reading it", () => {
    const { result } = renderHook(() => useDevMode());
    expect(result.current).toBe(false);
    act(() => setDevMode(true));
    expect(result.current).toBe(true);
    act(() => setDevMode(false));
    expect(result.current).toBe(false);
  });
});
