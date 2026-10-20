import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ItemsView from "./views/ItemsView.vue";
import { createAppI18n } from "./i18n";

const globalRule = { kind: "product", name: "<b>Spatula</b>", currency: "CZK", receipt_id: null,
  item_id: null, purchased_at: "2026-10-19T10:00:00Z", merchant: null,
  total_amount: "140.00", blocked_by: ["item"] };
const occurrence = { ...globalRule, kind: "item", receipt_id: "receipt-old", item_id: "line-old",
  purchased_at: "2020-01-02T10:00:00Z", merchant: "Globus Praha", total_amount: "40.00",
  blocked_by: ["product"] };

function stubApi(fail = false) {
  let local = true;
  let global = true;
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://test");
    if (url.pathname === "/api/v1/connections") return Response.json({ items: [] });
    if (init?.method === "PATCH") {
      if (fail) return Response.json({}, { status: 500 });
      if (url.pathname.endsWith("/items/line-old/exclusion")) {
        local = false;
        return Response.json({ items: [{ id: "line-old", exclusion_reason: global ? "product" : null }] });
      }
      global = false;
      return Response.json({ purchases: [] });
    }
    if (url.pathname === "/api/v1/items/exclusions") {
      const scope = url.searchParams.get("scope");
      const items = [global && globalRule, local && occurrence].filter((row) =>
        row && (scope === "all" || row.kind === scope));
      return Response.json({ items, total: items.length, limit: 30, offset: 0 });
    }
    return Response.json({ items: [], total: 0, limit: 30, offset: 0 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function setup(path = "/items?exclusion=excluded") {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/items", component: ItemsView },
    { path: "/items/detail", name: "item-detail", component: { template: "<div />" } },
    { path: "/receipts/:id", name: "receipt-detail", component: { template: "<div />" } },
  ] });
  await router.push(path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(ItemsView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { router, wrapper, client };
}

afterEach(() => vi.unstubAllGlobals());

describe("excluded item registry", () => {
  it("shows global rules and old one-off exclusions with receipt context", async () => {
    const fetcher = stubApi();
    const { wrapper, client } = await setup();
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/api/v1/items/exclusions?"), expect.anything());
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(2);
    expect(wrapper.text()).toContain("All purchases");
    expect(wrapper.text()).toContain("This receipt only");
    expect(wrapper.text()).toContain("Globus Praha");
    expect(wrapper.get('a[href="/receipts/receipt-old"]').text()).toContain("2020");
    expect(wrapper.get(".excluded-items").text()).toContain("<b>Spatula</b>");
    expect(wrapper.find("td b").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("persists subtype filters and sends the same purchase filters to the API", async () => {
    const fetcher = stubApi();
    const { router, wrapper, client } = await setup("/items?exclusion=excluded&from=2020-01-01&min=5&max=50&exclusion_scope=item");
    const urls = (): URL[] => fetcher.mock.calls.map(([url]) => new URL(String(url), "http://test"))
      .filter((url) => url.pathname === "/api/v1/items/exclusions");
    expect(urls().at(-1)?.searchParams.get("scope")).toBe("item");
    expect(urls().at(-1)?.searchParams.get("purchased_from")).toBe(new Date(2020, 0, 1).toISOString());
    expect(urls().at(-1)?.searchParams.get("price_min")).toBe("5");
    expect(urls().at(-1)?.searchParams.get("price_max")).toBe("50");
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(1);
    await wrapper.get(".exclusion-scope button").trigger("click");
    const globalOption = wrapper.findAll('.exclusion-scope [role="option"]').find((option) => option.text() === "Globally");
    expect(globalOption).toBeDefined();
    await globalOption!.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.exclusion_scope).toBe("product");
    expect(urls().at(-1)?.searchParams.get("scope")).toBe("product");
    wrapper.unmount(); client.clear();
  });

  it("restores an occurrence directly and explains the remaining global exclusion", async () => {
    const fetcher = stubApi();
    const { router, wrapper, client } = await setup();
    await wrapper.get('[data-exclusion="item"] button').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/receipts/receipt-old/items/line-old/exclusion", expect.objectContaining({
      method: "PATCH", body: JSON.stringify({ excluded: false }),
    }));
    expect(router.currentRoute.value.path).toBe("/items");
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(1);
    expect(wrapper.get('[role="status"].restoration-status').text()).toContain("One-off exclusion removed");
    expect(wrapper.get('[role="status"].restoration-status').text()).toContain("Excluded from all purchases");
    wrapper.unmount(); client.clear();
  });

  it("keeps the individual exception when the global rule is restored", async () => {
    const fetcher = stubApi();
    const { wrapper, client } = await setup();
    await wrapper.get('[data-exclusion="product"] button').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/items/exclusion", expect.objectContaining({
      method: "PATCH", body: JSON.stringify({ name: "<b>Spatula</b>", currency: "CZK", excluded: false }),
    }));
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(1);
    expect(wrapper.get('[role="status"].restoration-status').text()).toContain("Global exclusion removed");
    expect(wrapper.get('[role="status"].restoration-status').text()).toContain("Excluded on this receipt");
    wrapper.unmount(); client.clear();
  });

  it("keeps a failed restore visible and retryable", async () => {
    stubApi(true);
    const { wrapper, client } = await setup();
    await wrapper.get('[data-exclusion="item"] button').trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(2);
    expect(wrapper.get('[data-exclusion="item"] [role="alert"]').text()).toContain("Could not save");
    expect(wrapper.get('[data-exclusion="item"] button').attributes("disabled")).toBeUndefined();
    wrapper.unmount(); client.clear();
  });

  it("clamps the last page when its last one-off exclusion is restored", async () => {
    let restored = false;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = new URL(String(input), "http://test");
      if (url.pathname.endsWith("/connections")) return Response.json({ items: [] });
      if (init?.method === "PATCH") {
        restored = true;
        return Response.json({ items: [{ id: "line-old", exclusion_reason: null }] });
      }
      const offset = Number(url.searchParams.get("offset"));
      return Response.json({ items: restored && offset > 0 ? [] : [occurrence], total: restored ? 30 : 31, limit: 30, offset });
    }));
    const { router, wrapper, client } = await setup("/items?exclusion=excluded&exclusion_scope=item&offset=30");
    await wrapper.get('[data-exclusion="item"] button').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.offset).toBeUndefined();
    expect(wrapper.findAll(".excluded-items tbody tr")).toHaveLength(1);
    wrapper.unmount(); client.clear();
  });
});
