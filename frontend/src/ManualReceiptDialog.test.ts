import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ManualReceiptDialog from "./components/ManualReceiptDialog.vue";
import { createAppI18n } from "./i18n";
import type { ReceiptDetail } from "./api";

afterEach(() => vi.unstubAllGlobals());

async function render(fetch: typeof globalThis.fetch) {
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(ManualReceiptDialog, {
    attachTo: document.body,
    global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
  });
  await flushPromises();
  return wrapper;
}

const shops = () => Response.json({ items: [{ merchant: "Globus", provider: "globus", receipt_count: 3 }] });
const saved = () => Response.json({ items: [] }, { status: 201 });

describe("manual receipt dialog", () => {
  it("adds a receipt to an existing shop from the form", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(shops()).mockResolvedValueOnce(saved());
    const wrapper = await render(fetch);
    await wrapper.get('.app-select-trigger').trigger("click");
    await wrapper.get('[role="option"]').trigger("click");
    expect(wrapper.find("#manual-merchant").exists()).toBe(false);
    const [name, quantity, total] = wrapper.findAll(".manual-item input");
    expect([quantity?.attributes("type"), total?.attributes("type")]).toEqual(["number", "number"]);
    await name?.setValue("Káva");
    await quantity?.setValue("0.5");
    await total?.setValue("1249.5");
    expect(wrapper.get(".manual-items-footer strong").text()).toBe("624.75 CZK");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const [path, init] = fetch.mock.calls[1] ?? [];
    expect(path).toBe("/api/v1/receipts/manual");
    const receipt = JSON.parse(String(init?.body)).receipts[0];
    expect(receipt).toMatchObject({
      merchant: "Globus",
      provider: "globus",
      currency: "CZK",
      items: [{ name: "Káva", quantity: "0.5", unit: null, total_amount: "624.75" }],
    });
    expect(wrapper.emitted("saved")).toHaveLength(1);
    wrapper.unmount();
  });

  it("edits a manual receipt in place", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(shops()).mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const item = { name: "Káva", quantity: "2.000000", unit: null, unit_price: "60.000000", total_amount: "120.00" };
    const receipt = { id: "r1", merchant: "Globus", provider: "globus", purchased_at: "2026-10-29T09:15:00+01:00", currency: "CZK", items: [item] };
    const wrapper = mount(ManualReceiptDialog, {
      attachTo: document.body,
      props: { receipt: receipt as unknown as ReceiptDetail },
      global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
    });
    await flushPromises();
    expect(wrapper.find('input[type="radio"]').exists()).toBe(false);
    expect(wrapper.find("#manual-merchant").exists()).toBe(false);
    const [, quantity, price] = wrapper.findAll(".manual-item input");
    expect([(quantity?.element as HTMLInputElement).value, (price?.element as HTMLInputElement).value]).toEqual(["2", "60"]);
    await wrapper.get(".app-date input").trigger("click");
    await flushPromises();
    expect(document.querySelector("dialog .dp--menu")).not.toBeNull();
    await quantity?.setValue("3");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const [path, init] = fetch.mock.calls[1] ?? [];
    expect([path, init?.method]).toEqual(["/api/v1/receipts/r1", "PUT"]);
    expect(JSON.parse(String(init?.body))).toMatchObject({ merchant: "Globus", provider: "globus", items: [{ name: "Káva", quantity: "3", total_amount: "180.00" }] });
    expect(wrapper.emitted("saved")).toHaveLength(1);
    wrapper.unmount();
  });

  it("imports a JSON list and rejects invalid JSON", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(shops()).mockResolvedValueOnce(saved());
    const wrapper = await render(fetch);
    await wrapper.get('input[type="radio"][value="json"]').setValue(true);
    await wrapper.get("#manual-json").setValue("{oops");
    await wrapper.get("form").trigger("submit");
    expect(wrapper.get('[role="alert"]').text()).toContain("not valid");
    const receipts = [{ merchant: "Pekárna", purchased_at: "2026-10-29T08:00:00", items: [{ name: "Chléb", total_amount: "59.90" }] }];
    await wrapper.get("#manual-json").setValue(JSON.stringify({ receipts }));
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ receipts });
    wrapper.unmount();
  });
});
