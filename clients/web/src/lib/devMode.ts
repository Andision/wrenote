// Developer mode: the tools for looking at states the app normally hides.
//
// The first run, a slot with no model, a failed download — all of them are
// one-shot or need a file removed by hand, which made them the least-tested
// screens in the app. This is the switch that puts them back within reach.
//
// Found the way it is on a phone: tap the version number five times. Not
// discoverable by accident, not a secret, and it needs no setting for the
// setting. It persists, because the person who turned it on is working.
import { useSyncExternalStore } from "react";

const KEY = "wrenote.devMode";
/** Taps on the version line that turn it on, and where the hint starts. */
export const TAPS_REQUIRED = 5;
export const TAPS_BEFORE_HINT = 2;

function read(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false; // private mode / storage blocked
  }
}

let enabled = read();
const listeners = new Set<() => void>();

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setDevMode(next: boolean): void {
  if (next === enabled) return;
  enabled = next;
  try {
    window.localStorage.setItem(KEY, next ? "1" : "0");
  } catch {
    // Not fatal: it just won't survive a restart.
  }
  for (const fn of listeners) fn();
}

/** Whether developer mode is on. A store rather than component state: the
 *  settings rail and the panel both read it, and it outlives either. */
export function useDevMode(): boolean {
  return useSyncExternalStore(subscribe, () => enabled, () => false);
}
