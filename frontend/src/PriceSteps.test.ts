import { describe, expect, it } from "vitest";
import { stepPoints } from "./charts";

describe("price steps", () => {
  it("closes each step with a hidden point so hover keeps the step's own price", () => {
    const steps = stepPoints([
      { purchased_at: "2024-02-22T10:00:00Z", unit_price: "19.90" },
      { purchased_at: "2026-07-08T10:00:00Z", unit_price: "12.90" },
      { purchased_at: "2026-07-08T10:00:00Z", unit_price: "12.90" },
    ]);
    const july = Date.parse("2026-07-08T10:00:00Z");
    expect(steps).toEqual([
      { value: [Date.parse("2024-02-22T10:00:00Z"), 19.9], purchase: 0 },
      { value: [july - 1, 19.9], purchase: 0, symbol: "none" },
      { value: [july, 12.9], purchase: 1 },
      { value: [july, 12.9], purchase: 2 },
    ]);
  });
});
