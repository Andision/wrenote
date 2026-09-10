import { motion } from "motion/react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useT, type TFunction } from "@/i18n";
import {
  SOURCE_LANGUAGES,
  type LanguageOption,
} from "@/lib/languages";

const LABEL_BY_VALUE = new Map<string, string>(
  SOURCE_LANGUAGES.map((l) => [l.value, l.label]),
);

function lookupLabel(value: string | null | undefined, t: TFunction): string {
  if (!value) return "";
  const label = LABEL_BY_VALUE.get(value) ?? value;
  return label.startsWith("lang.") ? t(label) : label;
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
  const t = useT();
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
              {lookupLabel(v, t)}
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
            {l.label.startsWith("lang.") ? t(l.label) : l.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
