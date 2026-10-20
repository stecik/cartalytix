import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CalculatorView from "./views/CalculatorView.vue";
import { createAppI18n } from "./i18n";

const STORAGE_KEY = "cartalytix-calculator";
const rohlik = { name: "Rohlík", currency: "CZK", unit: null, receipt_count: 3, quantity: "17",
  total_amount: "43.00", average_price: "3.00", min_price: "2.00", max_price: "4.00",
  last_purchased_at: "2026-03-03T10:00:00Z" };
const bageta = { ...rohlik, name: "Bageta", total_amount: "25.00" };

function stubApi() {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.startsWith("/api/v1/connections")) return Response.json({ items: [] });
    if (url.startsWith("/api/v1/items/totals")) {
      const body = JSON.parse(String(init?.body)) as { items: Array<{ name: string }> };
      const items = [rohlik, bageta].filter((item) => body.items.some((selected) => selected.name === item.name));
      const total = items.reduce((sum, item) => sum + Number(item.total_amount), 0);
      return Response.json({ items, totals: items.length ? [{ currency: "CZK", total_amount: total.toFixed(2) }] : [] });
    }
    return Response.json({ items: [rohlik, bageta], total: 2, limit: 30, offset: 0 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function mountAt(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/calculator", name: "calculator", component: CalculatorView },
    { path: "/items/detail", name: "item-detail", component: { template: "<div />" } },
  ] });
  await router.push(path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(CalculatorView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { router, wrapper, client };
}

function totalsBodies(fetcher: ReturnType<typeof stubApi>): Array<Record<string, unknown>> {
  return fetcher.mock.calls
    .filter(([input]) => String(input).startsWith("/api/v1/items/totals"))
    .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
}

beforeEach(() => { localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("calculator", () => {
  it("shows each included amount alongside its original price and the net total", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ name: "Rohlík", currency: "CZK" }]));
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (input) => {
      if (String(input).startsWith("/api/v1/connections")) return Response.json({ items: [] });
      if (String(input).startsWith("/api/v1/items/totals")) return Response.json({ items: [{ ...rohlik, included_amount: "33.00" }], totals: [{ currency: "CZK", total_amount: "33.00" }] });
      return Response.json({ items: [{ ...rohlik, included_amount: "33.00" }], total: 1, limit: 30, offset: 0 });
    }));
    const { wrapper, client } = await mountAt("/calculator");
    expect(wrapper.get(".calculator-stats").text().replace(/\s/g, " ")).toContain("CZK 33.00");
    expect(wrapper.get(".selection-section .included-amount").text().replace(/\s/g, " ")).toBe("CZK 33.00");
    expect(wrapper.get(".ledger-section .included-amount").text().replace(/\s/g, " ")).toBe("CZK 33.00");
    expect(wrapper.get(".selection-section .strong").text().replace(/\s/g, " ")).toBe("CZK 43.00");
    wrapper.unmount(); client.clear();
  });

  it("sums ticked products for the chosen period and remembers them", async () => {
    const fetcher = stubApi();
    const { wrapper, client } = await mountAt("/calculator?from=2026-03-01&to=2026-03-31");
    expect(wrapper.text()).toContain("Tick products in the search results below to add them up.");
    expect(totalsBodies(fetcher)).toHaveLength(0);
    const boxes = wrapper.findAll(".ledger-section input[type=checkbox]");
    await boxes[0]?.setValue(true);
    await flushPromises();
    await wrapper.findAll(".ledger-section .item-row")[1]?.trigger("click");
    await flushPromises();
    expect(totalsBodies(fetcher).at(-1)).toEqual({
      items: [{ name: "Rohlík", currency: "CZK" }, { name: "Bageta", currency: "CZK" }],
      purchased_from: new Date(2026, 2, 1).toISOString(),
      purchased_to: new Date(2026, 3, 1).toISOString(),
    });
    expect(wrapper.get(".calculator-stats").text().replace(/\s/g, " ")).toContain("CZK 68.00");
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]")).toHaveLength(2);
    wrapper.unmount(); client.clear();
  });

  it("lists products not bought in the period and removes them", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([{ name: "Rohlík", currency: "CZK" }, { name: "Káva", currency: "EUR" }, { bad: 1 }]));
    stubApi();
    const { wrapper, client } = await mountAt("/calculator");
    expect(wrapper.get(".calculator-stats").text().replace(/\s/g, " ")).toContain("CZK 43.00");
    const missing = wrapper.get(".unpurchased-item");
    expect(missing.text()).toContain("Káva");
    await missing.trigger("click");
    await flushPromises();
    expect(wrapper.find(".unpurchased").exists()).toBe(false);
    await wrapper.findAll(".selection-section .section-heading button")[0]?.trigger("click");
    await flushPromises();
    expect(localStorage.getItem(STORAGE_KEY)).toBe("[]");
    wrapper.unmount(); client.clear();
  });
});
