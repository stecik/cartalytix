import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceiptDetailView from "./views/ReceiptDetailView.vue";
import ReceiptLedger from "./components/ReceiptLedger.vue";
import { createAppI18n } from "./i18n";

const receipt = { id: "receipt-1", connection_id: "account-1", provider: "lidl", external_id: "external-1",
  is_excluded: false, included_amount: "20.00", is_manual: false,
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

describe("bulk line exclusion", () => {
  it.each(["rendered", "detailed"] as const)("excludes selected lines through a previewed batch in the %s view", async (view) => {
    const items = [
      { ...receipt.items[0], is_excluded: false, exclusion_reason: null, included_amount: "26.00" },
      { ...receipt.items[0], id: "item-2", name: "Pears", is_excluded: false, exclusion_reason: null, included_amount: "10.00" },
      { ...receipt.items[0], id: "item-3", name: "Milk", is_excluded: false, exclusion_reason: null, included_amount: "5.00" },
    ];
    const batch = { batch_id: null, dry_run: true, replayed: false, state: "state-1", receipts: [], items: [],
      changes: [], totals: [{ currency: "CZK", before_amount: "20.00", after_amount: "-16.00", difference: "-36.00" }] };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) =>
      String(input) === "/api/v1/changes" ? Response.json({ ...batch, changes: [{ changed: true }] }) : Response.json({ ...receipt, items }));
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    client.setQueryData(["session"], { id: "user-1", receipt_view: view });
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    expect(wrapper.find(".selection-bar").exists()).toBe(false);
    await wrapper.get('input[aria-label="Select <b>Apples</b>"]').setValue(true);
    await wrapper.get('input[aria-label="Select Milk"]').setValue(true);
    expect(wrapper.get(".selection-bar").text()).toContain("2 items selected");
    expect(wrapper.find('[data-action="restore-selected"]').exists()).toBe(false);
    await wrapper.get('[data-action="exclude-selected"]').trigger("click");
    await flushPromises();
    const changeCalls = () => fetcher.mock.calls.filter(([input]) => String(input) === "/api/v1/changes").map(([, init]) => JSON.parse(String(init?.body)));
    expect(changeCalls()[0]).toEqual({ dry_run: true, operations: [
      { kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-1", excluded: true },
      { kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-3", excluded: true },
    ] });
    await wrapper.get('[data-action="apply-changes"]').trigger("click");
    await flushPromises();
    expect(changeCalls()[1]).toMatchObject({ dry_run: false, expected_state: "state-1" });
    expect(wrapper.find(".selection-bar").exists()).toBe(false);
    expect(wrapper.find('[data-action="apply-changes"]').exists()).toBe(false);
    expect(wrapper.find('[data-exclusion="item"] button').exists()).toBe(true);
    wrapper.unmount(); client.clear();
  });
});

describe("partly counted items", () => {
  it("counts part of a line and shows the prorated amount", async () => {
    let counted: string | null = null;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (init?.method === "PATCH") counted = JSON.parse(String(init.body)).included_quantity;
      return Response.json({ ...receipt, included_amount: counted ? "5.00" : "20.00",
        items: receipt.items.map((item) => ({ ...item, quantity: "4", is_weight: false, is_excluded: false, exclusion_reason: null, included_quantity: counted, included_amount: counted ? "5.00" : "20.00" })),
      });
    });
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    const wrapper = mount(ReceiptDetailView, { attachTo: document.body, global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    await wrapper.get('[data-action="count-part"]').trigger("click");
    const input = document.querySelector<HTMLInputElement>("#included-quantity");
    expect(input?.step).toBe("1");
    input!.value = "1";
    input!.dispatchEvent(new Event("input"));
    input!.form!.dispatchEvent(new Event("submit"));
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/receipts/receipt-1/items/item-1/included-quantity", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ included_quantity: "1" }) }));
    expect(wrapper.get(".included-part").text()).toContain("Counted 1 kg of 4 kg");
    expect(wrapper.get(".included-part").text()).toContain("5.00");
    wrapper.unmount(); client.clear();
  });
});

describe("receipt change history", () => {
  it("shows the latest changes of this receipt and loads more on request", async () => {
    const batch = (id: string) => ({ id, created_at: "2026-10-30T12:00:00Z", source: "user", client_name: null, undo_of_id: null, totals: [],
      entries: [{ id: `entry-${id}`, kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-1", name: "Apples", currency: "CZK", before: false, after: true, undone_by_id: null }] });
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname !== "/api/v1/changes") return Response.json(receipt);
      const limit = Number(url.searchParams.get("limit"));
      return Response.json({ items: Array.from({ length: Math.min(limit, 5) }, (_, index) => batch(`batch-${index}`)), total: 5, limit, offset: 0 });
    });
    vi.stubGlobal("fetch", fetcher);
    const router = await routerForReceipt();
    const client = detailedClient();
    const wrapper = mount(ReceiptDetailView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/changes?receipt_id=receipt-1&limit=3", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(wrapper.findAll(".receipt-history .change-batch")).toHaveLength(3);
    expect(wrapper.get(".receipt-history").text()).toContain("Excluded line Apples");
    await wrapper.get('[data-action="more-changes"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/changes?receipt_id=receipt-1&limit=13", expect.anything());
    expect(wrapper.findAll(".receipt-history .change-batch")).toHaveLength(5);
    expect(wrapper.find('[data-action="more-changes"]').exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });
});
