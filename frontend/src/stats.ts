import type { PresetPeriod } from "./api";

export const PRESET_PERIODS: readonly PresetPeriod[] = ["today", "month", "year", "30d", "90d", "12m", "all"];

export function numberLocale(locale: string): string {
  return locale === "cs" ? "cs-CZ" : "en-GB";
}

export function formatMoney(
  amount: string | number,
  currency: string,
  locale: string,
  compact = false,
): string {
  return new Intl.NumberFormat(numberLocale(locale), {
    style: "currency",
    currency,
    ...(compact ? { notation: "compact", maximumFractionDigits: 1 } : {}),
  }).format(Number(amount));
}

export function formatMonth(month: string, locale: string, withYear: boolean): string {
  const [year, number] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(numberLocale(locale), {
    month: "short",
    ...(withYear ? { year: "2-digit" } : {}),
  }).format(new Date(year ?? 2000, (number ?? 1) - 1, 1));
}

export function formatLongMonth(month: string, locale: string): string {
  const [year, number] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(numberLocale(locale), {
    month: "long",
    year: "numeric",
  }).format(new Date(year ?? 2000, (number ?? 1) - 1, 1));
}

function parseDay(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year ?? 2000, (month ?? 1) - 1, date ?? 1);
}

export function formatDay(day: string, locale: string): string {
  return new Intl.DateTimeFormat(numberLocale(locale), { day: "numeric", month: "numeric" }).format(parseDay(day));
}

export function formatLongDay(day: string, locale: string): string {
  return new Intl.DateTimeFormat(numberLocale(locale), { dateStyle: "long" }).format(parseDay(day));
}

/** Local calendar date as YYYY-MM-DD, days from today. */
export function isoDay(days = 0): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((part) => String(part).padStart(2, "0")).join("-");
}

export function weekdayNames(locale: string): string[] {
  const format = new Intl.DateTimeFormat(numberLocale(locale), { weekday: "short" });
  // 2024-01-01 is a Monday; the API orders weekdays Monday first.
  return Array.from({ length: 7 }, (_, day) => format.format(new Date(2024, 0, 1 + day)));
}

export function percentChange(current: number, previous: number | null): number | null {
  if (previous === null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}
