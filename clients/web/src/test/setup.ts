// What jsdom doesn't provide but the app assumes.
//
// Kept to genuine environment gaps: anything stubbed here is behaviour a test
// can no longer catch, so the list should stay short and each entry should say
// why jsdom can't do it.
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());

// jsdom implements neither; motion/react and the scroll-into-view calls in the
// transcript use them.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

// matchMedia is used by next-themes and by a couple of layout hooks.
globalThis.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
})) as unknown as typeof matchMedia;
