import { describe, expect, it } from "vitest";
import { createAppI18n } from "./i18n";
import { connectionLabel, sourceName } from "./names";

describe("display names", () => {
  it("labels accounts with the store and the optional account name", () => {
    expect(connectionLabel({ provider: "fermato", name: "Osobní" })).toBe("Fermato · Osobní");
    expect(connectionLabel({ provider: "mixit", name: "" })).toBe("Mixit");
    expect(connectionLabel({ provider: "mixit", name: "", account_label: "a@example.com" })).toBe("Mixit · a@example.com");
  });

  it("translates connector adjustment rows and keeps source names", () => {
    const { t } = createAppI18n("cs").global;
    expect(sourceName("Discounts and credits", t)).toBe("Slevy a kredity");
    expect(sourceName("Shipping and fees", t)).toBe("Doprava a poplatky");
    expect(sourceName("Sleva 15 %", t)).toBe("Sleva 15 %");
  });
});
