"use client";
import { useLocale } from "./LocaleProvider";

/**
 * Tiny provenance badge for a skill's origin.
 *
 * Accepts the `Skill.source` / `ToolCallInfo.source` string from the backend
 * (values: "local", "blob") and renders a coloured chip.
 * Returns null for empty / unrecognised sources so builtin tools stay clean.
 */
export function SourceBadge({
  source,
  size = "xs",
}: {
  source?: string | null;
  size?: "xs" | "sm";
}) {
  const { t } = useLocale();
  if (!source) return null;

  const kind = source;

  const styles: Record<string, string> = {
    blob: "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800",
    local: "bg-slate-100 text-slate-600 border-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700",
  };

  const label = kind.toUpperCase();
  const title = t("source", { name: kind });
  const palette = styles[kind] ?? styles.local;
  const sizeCls = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-1.5 py-0.5 text-[9px]";

  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-sm border font-semibold uppercase tracking-wide ${palette} ${sizeCls}`}
    >
      {label}
    </span>
  );
}
