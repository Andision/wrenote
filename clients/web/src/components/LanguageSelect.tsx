import { motion } from "motion/react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export interface LanguageOption {
  value: string;
  label: string;
}

// Shared between source/target dropdowns. "auto" only appears in source.
export const TARGET_LANGUAGES: LanguageOption[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "ru", label: "Русский" },
];

export const SOURCE_LANGUAGES: LanguageOption[] = [
  { value: "auto", label: "Auto detect" },
  ...TARGET_LANGUAGES,
];

const LABEL_BY_VALUE = new Map<string, string>(
  SOURCE_LANGUAGES.map((l) => [l.value, l.label]),
);

function lookupLabel(value: string | null | undefined): string {
  if (!value) return "";
  return LABEL_BY_VALUE.get(value) ?? value;
}

interface LanguageSelectProps {
  value: string;
  options: LanguageOption[];
  onChange: (next: string) => void;
  size?: "default" | "compact";
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}

/**
 * Language dropdown used in pre-flight (default) and TopBar (compact).
 * Base-ui's SelectValue doesn't auto-map value→label, so we pass a render fn.
 */
export function LanguageSelect({
  value,
  options,
  onChange,
  size = "default",
  disabled,
  className,
  ariaLabel,
}: LanguageSelectProps) {
  const triggerSize =
    size === "compact" ? "h-7 px-2 text-[12px]" : "h-9 px-3 text-sm";
  return (
    <Select
      value={value}
      onValueChange={(v) => {
        if (typeof v === "string") onChange(v);
      }}
      disabled={disabled}
    >
      <SelectTrigger
        aria-label={ariaLabel}
        className={cn(
          "min-w-[7rem] justify-between gap-2 transition-colors",
          triggerSize,
          className,
        )}
      >
        <SelectValue>
          {(v: string | null) => (
            <motion.span
              key={v ?? "empty"}
              initial={{ opacity: 0, y: -2 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.14 }}
              className="font-medium"
            >
              {lookupLabel(v)}
            </motion.span>
          )}
        </SelectValue>
      </SelectTrigger>
      {/* Force "appears below trigger" instead of base-ui's default
          alignItemWithTrigger=true (which centers the popup on the
          selected row and pushes it half above the trigger). */}
      <SelectContent
        side="bottom"
        align="start"
        sideOffset={6}
        alignItemWithTrigger={false}
      >
        {options.map((l) => (
          <SelectItem key={l.value} value={l.value}>
            {l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
