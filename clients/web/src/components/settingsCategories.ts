// The Settings rail's categories. Data, not a component, and in its own file
// so a test can import the list and hold every id to having a label: the ids
// are composed into `settings.cat.<id>` at render, which is invisible to a
// scan of the source for literal `t(...)` keys — that is how `settings.cat.models`
// shipped with no message and rendered as its own key.
import {
  BookMarked,
  Boxes,
  Cpu,
  FlaskConical,
  Gauge,
  Info,
  Mic,
  Scissors,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";

export type CategoryId =
  | "general"
  | "recording"
  | "glossary"
  | "models"
  | "about"
  | "tuning"
  | "engines"
  | "compute"
  | "dev";

/** What the rail shows without being asked, grouped the way someone using
 *  the app would look for things: the app itself, what a recording does,
 *  the words it should know, what runs it, and what it is.
 *
 *  Not one category per subsystem. "Segmentation" and "Real-time" were two
 *  entries of two settings each, both of them thresholds on the same live
 *  pipeline; they are one Tuning panel under Advanced now. */
export const SETTINGS_CATEGORIES: { id: CategoryId; icon: LucideIcon }[] = [
  { id: "general", icon: SlidersHorizontal },
  { id: "recording", icon: Mic },
  { id: "glossary", icon: BookMarked },
  { id: "models", icon: Boxes },
  { id: "about", icon: Info },
];

/** Behind an "Advanced" disclosure. Not hidden — a power user tuning the
 *  segmentation is not debugging, so developer mode would be the wrong place
 *  — but not the first thing you meet either. No warning dialog in front:
 *  people learn to click those away, and a "reset to defaults" in each panel
 *  is the safety net that actually undoes a mistake. */
export const ADVANCED_CATEGORIES: { id: CategoryId; icon: LucideIcon }[] = [
  { id: "tuning", icon: Scissors },
  { id: "compute", icon: Gauge },
  { id: "engines", icon: Cpu },
];

/** Only listed while developer mode is on (lib/devMode.ts). Kept out of
 *  SETTINGS_CATEGORIES so nothing has to filter the normal list. */
export const DEV_CATEGORY: { id: CategoryId; icon: LucideIcon } = {
  id: "dev",
  icon: FlaskConical,
};
