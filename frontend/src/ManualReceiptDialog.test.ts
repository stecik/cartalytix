import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ManualReceiptDialog from "./components/ManualReceiptDialog.vue";
import { createAppI18n } from "./i18n";

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

const shops = Response.json({ items: [{ merchant: "Globus", provider: "globus", receipt_count: 3 }] });
const saved = () => Response.json({ items: [] }, { status: 201 });

describe("manual receipt dialog", () => {
  it("adds a receipt to an existing shop from the form", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(shops).mockResolvedValueOnce(saved());
    const wrapper = await render(fetch);
    await wrapper.get('.app-select-trigger').trigger("click");
    await wrapper.get('[role="option"]').trigger("click");
    expect(wrapper.find("#manual-merchant").exists()).toBe(false);
    const [name, quantity, total] = wrapper.findAll(".manual-item input");
    await name?.setValue("Káva");
    await quantity?.setValue("5");
    await total?.setValue("1249,50");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const [path, init] = fetch.mock.calls[1] ?? [];
    expect(path).toBe("/api/v1/receipts/manual");
    const receipt = JSON.parse(String(init?.body)).receipts[0];
    expect(receipt).toMatchObject({
      merchant: "Globus",
      provider: "globus",
      currency: "CZK",
      items: [{ name: "Káva", quantity: "5", total_amount: "1249.50" }],
    });
    expect(wrapper.emitted("saved")).toHaveLength(1);
    wrapper.unmount();
  });

  it("imports a JSON list and rejects invalid JSON", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(shops).mockResolvedValueOnce(saved());
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
