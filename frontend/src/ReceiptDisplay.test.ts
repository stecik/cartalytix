import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceiptDetailView from "./views/ReceiptDetailView.vue";
import SettingsView from "./views/SettingsView.vue";
import ReceiptLedger from "./components/ReceiptLedger.vue";
import { createAppI18n } from "./i18n";
import { providerLogos } from "./providerLogos";
import type { User } from "./api";

const user: User = { id: "user-1", email: "owner@example.com", locale: "en", receipt_view: "rendered", theme: "system" };
const receipt = { id: "receipt-1", connection_id: "account-1", provider: "lidl", external_id: "R-42",
  purchased_at: "2026-10-17T10:00:00Z", merchant: "Lidl Praha", total_amount: "20.00", currency: "CZK",
  rounding_amount: "0.20", quality_issues: [], source_format: "htmlPrintedReceipt",
  items: [{ id: "item-1", position: 1, name: "Apples", quantity: "0.5230", unit: "kg", unit_price: "60.0000", total_amount: "26.00", original_amount: "31.38", deposit_amount: null, printed_net_amount: null, is_weight: true, source_code: null, warranty: null }],
  discounts: [{ id: "discount-1", item_id: "item-1", name: "Coupon", amount: "5.38" }],
};

async function setup(component: typeof ReceiptDetailView | typeof SettingsView, view: User["receipt_view"] = "rendered") {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/receipts", name: "receipts", component: { template: "<div />" } },
    { path: "/receipts/:id", name: "receipt-detail", component: ReceiptDetailView },
    { path: "/settings", name: "settings", component: SettingsView },
  ] });
  await router.push(component === SettingsView ? "/settings" : "/receipts/receipt-1");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000 } } });
  client.setQueryData(["session"], { ...user, receipt_view: view });
  const i18n = createAppI18n("en");
  const wrapper = mount(component, { global: { plugins: [router, i18n, [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { wrapper, client, i18n };
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("receipt display", () => {
  it("shows the connector logo next to each store", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/receipts/:id", name: "receipt-detail", component: { template: "<div />" } }] });
    const wrapper = mount(ReceiptLedger, { props: { receipts: [receipt] }, global: { plugins: [router, createAppI18n("en")] } });
    expect(wrapper.get("tbody tr img").attributes("src")).toBe(providerLogos.lidl);
    wrapper.unmount();
  });

  it("renders the receipt as printed paper when the account prefers it", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => Response.json(receipt)));
    const { wrapper, client } = await setup(ReceiptDetailView);
    const paper = wrapper.get(".receipt-paper");
    expect(paper.get("img").attributes("src")).toBe(providerLogos.lidl);
    expect(paper.text()).toContain("Lidl Praha");
    expect(paper.text()).toContain("Apples");
    expect(paper.text().replace(/\s/g, " ")).toContain("0.523 kg × CZK 60.00");
    expect(paper.text()).toContain("Coupon");
    expect(paper.text()).toContain("R-42");
    expect(paper.get(".receipt-paper-total").text().replace(/\s/g, " ")).toContain("CZK 20.00");
    expect(wrapper.find(".receipt-item-list").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("switches and saves the view from the receipt toggle", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      init?.method === "PATCH" ? Response.json({ ...user, receipt_view: "detailed" }) : Response.json(receipt));
    vi.stubGlobal("fetch", fetcher);
    const { wrapper, client } = await setup(ReceiptDetailView);
    const toggle = wrapper.get(".view-toggle");
    expect((toggle.get('input[value="rendered"]').element as HTMLInputElement).checked).toBe(true);
    await toggle.get('input[value="detailed"]').setValue(true);
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/auth/me", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ receipt_view: "detailed" }) }));
    expect(wrapper.find(".receipt-paper").exists()).toBe(false);
    expect(wrapper.find(".receipt-item-list").exists()).toBe(true);
    wrapper.unmount(); client.clear();
  });

  it("keeps the detailed view when the account prefers it", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => Response.json(receipt)));
    const { wrapper, client } = await setup(ReceiptDetailView, "detailed");
    expect(wrapper.find(".receipt-paper").exists()).toBe(false);
    expect(wrapper.find(".receipt-item-list").exists()).toBe(true);
    wrapper.unmount(); client.clear();
  });
});

describe("settings", () => {
  it("saves receipt display and language to the account", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      Response.json({ ...user, ...JSON.parse(String(init?.body)) }));
    vi.stubGlobal("fetch", fetcher);
    const { wrapper, client, i18n } = await setup(SettingsView);
    expect((wrapper.get('input[value="rendered"]').element as HTMLInputElement).checked).toBe(true);
    await wrapper.get('input[value="detailed"]').setValue(true);
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/auth/me", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ receipt_view: "detailed" }) }));
    expect(client.getQueryData<User>(["session"])?.receipt_view).toBe("detailed");
    await wrapper.get('button[aria-label="Language"]').trigger("click");
    await wrapper.get('[role="option"]:last-child').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/auth/me", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ locale: "cs" }) }));
    expect(i18n.global.locale.value).toBe("cs");
    expect(wrapper.get("h1").text()).toBe("Nastavení");
    wrapper.unmount(); client.clear();
  });

  it("rolls back a setting the server rejects", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => Response.json({ detail: "validation_error" }, { status: 422 })));
    const { wrapper, client } = await setup(SettingsView);
    await wrapper.get('input[value="detailed"]').setValue(true);
    await flushPromises();
    expect(client.getQueryData<User>(["session"])?.receipt_view).toBe("rendered");
    expect(wrapper.get('[role="alert"]').text()).toBe("Settings could not be saved. Try again.");
    wrapper.unmount(); client.clear();
  });
});
