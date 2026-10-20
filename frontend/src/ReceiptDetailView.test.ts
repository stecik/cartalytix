import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceiptDetailView from "./views/ReceiptDetailView.vue";
import ReceiptLedger from "./components/ReceiptLedger.vue";
import { createAppI18n } from "./i18n";

const receipt = { id: "receipt-1", connection_id: "account-1", provider: "lidl", external_id: "external-1",
  is_excluded: false, included_amount: "20.00",
  purchased_at: "2026-10-17T10:00:00Z", merchant: "Lidl Praha", total_amount: "20.00", currency: "CZK",
  rounding_amount: "0.20", quality_issues: [], source_format: "legacy",
  items: [{ id: "item-1", position: 1, name: "<b>Apples</b>", quantity: "0.5230", unit: "kg", unit_price: "60.0000", total_amount: "26.00", original_amount: "31.38", deposit_amount: "0.00", printed_net_amount: "26.00", is_weight: true, source_code: "12345", warranty: null }],
  discounts: [{ id: "discount-1", item_id: "item-1", name: "Coupon", amount: "5.38" }, { id: "discount-2", item_id: null, name: "Correction", amount: "-1.00" }],
};

async function routerForReceipt() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/receipts", name: "receipts", component: { template: "<div />" } },
    { path: "/receipts/:id", name: "receipt-detail", component: ReceiptDetailView },
    { path: "/items/detail", name: "item-detail", component: { template: "<div />" } },
  ] });
  await router.push("/receipts/receipt-1");
  return router;
}

function detailedClient(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["session"], { id: "user-1", email: "owner@example.com", locale: "en", receipt_view: "detailed" });
  return client;
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("receipt details", () => {
  it("excludes and restores a receipt without changing its original total", async () => {
    let excluded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (init?.method === "PATCH") excluded = JSON.parse(String(init.body)).excluded;
      return Response.json({ ...receipt, is_excluded: excluded, included_amount: excluded ? "0.00" : "20.00" });
    });
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    client.setQueryData(["overview"], { marker: "old" });
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    await wrapper.get('[data-exclusion="receipt"] button').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/receipts/receipt-1/exclusion", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ excluded: true }) }));
    expect(wrapper.get('[data-exclusion="receipt"] button').text()).toBe("Restore receipt");
    expect(wrapper.get(".receipt-total").text()).toContain("20.00");
    expect(wrapper.get(".included-total").text()).toContain("0.00");
    expect(client.getQueryState(["overview"])?.isInvalidated).toBe(true);
    await wrapper.get('[data-exclusion="receipt"] button').trigger("click");
    await flushPromises();
    expect(wrapper.get(".included-total").text()).toContain("20.00");
    wrapper.unmount(); client.clear();
  });

  it.each(["rendered", "detailed"] as const)("excludes a single occurrence in the %s view", async (view) => {
    let excluded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (init?.method === "PATCH") excluded = JSON.parse(String(init.body)).excluded;
      return Response.json({ ...receipt, is_excluded: false, included_amount: excluded ? "0.00" : "20.00",
        items: receipt.items.map((item) => ({ ...item, is_excluded: excluded, exclusion_reason: excluded ? "item" : null, included_amount: excluded ? "0.00" : "20.00" })),
      });
    });
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    client.setQueryData(["session"], { id: "user-1", receipt_view: view });
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    await wrapper.get('[data-exclusion="item"] button').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/receipts/receipt-1/items/item-1/exclusion", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ excluded: true }) }));
    expect(wrapper.get('[data-exclusion="item"] button').text()).toBe("Restore this item");
    expect(wrapper.get(".excluded-row").text()).toContain("<b>Apples</b>");
    wrapper.unmount(); client.clear();
  });

  it("explains global exclusion and keeps failed writes retryable", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      init?.method === "PATCH" ? Response.json({}, { status: 500 }) : Response.json({ ...receipt,
        items: receipt.items.map((item) => ({ ...item, is_excluded: false, exclusion_reason: "product", included_amount: "0.00" })),
      })));
    const router = await routerForReceipt();
    const client = detailedClient();
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    expect(wrapper.text()).toContain("Excluded from all purchases");
    await wrapper.get('[data-exclusion="item"] button').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-exclusion="item"] [role="alert"]').text()).toContain("Could not save");
    expect(wrapper.get('[data-exclusion="item"] button').attributes("disabled")).toBeUndefined();
    wrapper.unmount(); client.clear();
  });

  it("shows stored item values, coupon basis and signed discounts without interpreting HTML", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(receipt));
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/receipts/receipt-1", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(wrapper.get("h1").text()).toBe("Lidl Praha");
    expect(wrapper.text()).toContain("Before coupon");
    expect(wrapper.text()).toContain("0.523 kg");
    expect(wrapper.text()).toContain("Coupon");
    expect(wrapper.text()).toContain("Correction");
    expect(wrapper.text()).toContain("-CZK");
    expect(wrapper.text()).toContain("<b>Apples</b>");
    expect(wrapper.find("b").exists()).toBe(false);
    expect(wrapper.get('a[href="/receipts"]').text()).toBe("Back to receipts");
    wrapper.unmount(); client.clear();
  });

  it("makes every receipt reachable through a native link", async () => {
    const router = await routerForReceipt();
    const wrapper = mount(ReceiptLedger, { props: { receipts: [receipt] }, global: { plugins: [router, createAppI18n("en")] } });
    expect(wrapper.get('a[href="/receipts/receipt-1"]').text()).toBe("Lidl Praha");
    await router.push("/receipts");
    await wrapper.get("tbody tr").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/receipts/receipt-1");
    wrapper.unmount();
  });

  it("translates the piece unit in Czech", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(Response.json({ ...receipt,
      items: receipt.items.map((item) => ({ ...item, quantity: "2", unit: "piece", is_weight: false })),
    })));
    const router = await routerForReceipt();
    const client = detailedClient();
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("cs"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    expect(wrapper.text()).toContain("2 ks");
    expect(wrapper.text()).not.toContain("piece");
    wrapper.unmount(); client.clear();
  });
});
