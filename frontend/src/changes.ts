import type { ChangeHistoryEntry } from "./api";
import { sourceName } from "./names";
import { numberLocale } from "./stats";

type Translate = (key: string, named: Record<string, unknown>) => string;

const sentenceKeys: Record<Exclude<ChangeHistoryEntry["kind"], "item_included_quantity">, [excluded: string, restored: string]> = {
  receipt_exclusion: ["changeExcludedReceipt", "changeRestoredReceipt"],
  item_exclusion: ["changeExcludedLine", "changeRestoredLine"],
  product_exclusion: ["changeExcludedProduct", "changeRestoredProduct"],
};

function quantity(value: string, locale: string): string {
  return new Intl.NumberFormat(numberLocale(locale), { maximumFractionDigits: 4 }).format(Number(value));
}

/** Reads a recorded change as a sentence, e.g. "Excluded line Burger". */
export function changeSentence(entry: Pick<ChangeHistoryEntry, "kind" | "name" | "after">, t: Translate, locale: string): string {
  const name = sourceName(entry.name, (key) => t(key, {}));
  if (entry.kind === "item_included_quantity") {
    return typeof entry.after === "string"
      ? t("changeCountedPart", { counted: quantity(entry.after, locale), name })
      : t("changeCountedWhole", { name });
  }
  const [excluded, restored] = sentenceKeys[entry.kind];
  return t(entry.after ? excluded : restored, { name });
}

/** A recorded value: the excluded flag, or a counted quantity where null counts the whole line. */
export function changeValue(value: boolean | string | null, t: Translate, locale: string): string {
  if (typeof value === "boolean") return t(value ? "changeValueExcluded" : "changeValueIncluded", {});
  return value === null ? t("changeValueWhole", {}) : t("changeValueCounted", { counted: quantity(value, locale) });
}
