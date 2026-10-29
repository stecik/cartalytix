export const SYNC_INTERVALS = [60, 360, 1440] as const;

type Translate = (key: string, named?: Record<string, unknown>) => string;

const LABELS: Record<(typeof SYNC_INTERVALS)[number], string> = { 60: "everyHour", 360: "everySixHours", 1440: "everyDay" };

/** Preset intervals as select options; a stored non-preset value stays selectable. */
export function syncIntervalOptions(t: Translate, current?: number | null): { value: string; label: string }[] {
  const custom = current && !SYNC_INTERVALS.some((option) => option === current)
    ? [{ value: String(current), label: t("everyMinutes", { count: current }) }]
    : [];
  return [...SYNC_INTERVALS.map((value) => ({ value: String(value), label: t(LABELS[value]) })), ...custom];
}
