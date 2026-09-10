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
  Scissors,
  SlidersHorizontal,
  Zap,
  type LucideIcon,
} from "lucide-react";

export type CategoryId =
  | "general"
  | "segmentation"
  | "realtime"
  | "glossary"
  | "models"
  | "engines"
  | "compute"
  | "dev";

/** What the rail shows without being asked. Everything a user who just wants
 *  a transcript would recognise. */
export const SETTINGS_CATEGORIES: { id: CategoryId; icon: LucideIcon }[] = [
  { id: "general", icon: SlidersHorizontal },
  { id: "glossary", icon: BookMarked },
  { id: "models", icon: Boxes },
];

/** Behind an "Advanced" disclosure. Not hidden — a power user tuning the
 *  segmentation is not debugging, so developer mode would be the wrong place
 *  — but not the first thing you meet either. No warning dialog in front:
 *  people learn to click those away, and a "reset to defaults" in each panel
 *  is the safety net that actually undoes a mistake. */
export const ADVANCED_CATEGORIES: { id: CategoryId; icon: LucideIcon }[] = [
  { id: "segmentation", icon: Scissors },
  { id: "realtime", icon: Zap },
  { id: "engines", icon: Cpu },
  { id: "compute", icon: Gauge },
];

/** Only listed while developer mode is on (lib/devMode.ts). Kept out of
 *  SETTINGS_CATEGORIES so nothing has to filter the normal list. */
export const DEV_CATEGORY: { id: CategoryId; icon: LucideIcon } = {
  id: "dev",
  icon: FlaskConical,
};
