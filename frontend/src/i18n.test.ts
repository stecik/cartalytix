import { describe, expect, it } from "vitest";
import { createAppI18n, messages } from "./i18n";

describe("localization", () => {
  it("switches all source keys to Czech without missing keys or placeholders", () => {
    expect(Object.keys(messages.cs).sort()).toEqual(
      Object.keys(messages.en).sort(),
    );
    for (const key of Object.keys(messages.en) as Array<
      keyof typeof messages.en
    >) {
      expect(new Set(messages.cs[key].match(/\{\w+\}/g) ?? [])).toEqual(
        new Set(messages.en[key].match(/\{\w+\}/g) ?? []),
      );
    }
    const i18n = createAppI18n("en");
    expect(i18n.global.t("signIn")).toBe("Sign in");
    i18n.global.locale.value = "cs";
    expect(i18n.global.t("signIn")).toBe("Přihlásit se");
    expect(i18n.global.fallbackLocale.value).toBe("en");
    expect(i18n.global.t("receiptCount", 0)).toBe("Žádné účtenky");
    expect(i18n.global.t("receiptCount", 1)).toBe("1 účtenka");
    expect(i18n.global.t("receiptCount", 3)).toBe("3 účtenky");
    expect(i18n.global.t("receiptCount", 5)).toBe("5 účtenek");
  });
});
