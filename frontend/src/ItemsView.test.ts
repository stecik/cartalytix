import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { h } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ItemsView from "./views/ItemsView.vue";
import ItemDetailView from "./views/ItemDetailView.vue";
import { createAppI18n } from "./i18n";

vi.mock("./components/PriceHistoryChart.vue", () => ({
  __esModule: true,
  default: { props: ["purchases", "currency"], setup: (props: { purchases: unknown[] }) => () => h("div", { class: "chart-stub", "data-count": props.purchases.length }) },
}));

const item = { name: "<b>Rohlík</b>", currency: "CZK", unit: null, receipt_count: 3, quantity: "17",
  total_amount: "43.00", average_price: "3.00", min_price: "2.00", max_price: "4.00",
  last_purchased_at: "2026-03-03T10:00:00Z" };
const detail = { ...item, purchases: [
  { receipt_id: "receipt-2", purchased_at: "2026-03-03T10:00:00Z", merchant: "Billa", quantity: "2", unit: null, unit_price: "4.00", total_amount: "8.00" },
  { receipt_id: "receipt-1", purchased_at: "2026-03-01T10:00:00Z", merchant: "Lidl", quantity: "10", unit: null, unit_price: "2.00", total_amount: "20.00" },
] };

function stubApi() {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = String(input);
    if (url.startsWith("/api/v1/connections")) return Response.json({ items: [] });
    if (url.startsWith("/api/v1/items/exclusions")) return Response.json({ items: [], total: 0, limit: 30, offset: 0 });
    if (url.startsWith("/api/v1/items/detail")) {
      return url.includes("currency=CZK") ? Response.json(detail) : Response.json({}, { status: 404 });
    }
    return Response.json({ items: [item], total: 1, limit: 30, offset: 0 });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function mountAt(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/items", name: "items", component: ItemsView },
    { path: "/items/detail", name: "item-detail", component: ItemDetailView },
    { path: "/receipts/:id", name: "receipt-detail", component: { template: "<div />" } },
  ] });
  await router.push(path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const component = path.startsWith("/items/detail") ? ItemDetailView : ItemsView;
  const wrapper = mount(component, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { router, wrapper, client };
}

function itemUrls(fetcher: ReturnType<typeof stubApi>): URL[] {
  return fetcher.mock.calls.map(([input]) => new URL(String(input), "http://test"))
    .filter((url) => url.pathname === "/api/v1/items");
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("items ledger", () => {
  it("returns to a valid page after excluding the last product on the final page", async () => {
    let excluded = false;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input).startsWith("/api/v1/connections")) return Response.json({ items: [] });
      if (init?.method === "PATCH") { excluded = true; return new Response(null, { status: 204 }); }
      const offset = Number(new URL(String(input), "http://test").searchParams.get("offset"));
      return Response.json({ items: excluded && offset > 0 ? [] : [item], total: excluded ? 30 : 31, limit: 30, offset });
    }));
    const { router, wrapper, client } = await mountAt("/items?offset=30");
    await wrapper.get('tbody [data-exclusion="product"] button').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.offset).toBeUndefined();
    expect(wrapper.findAll("tbody tr")).toHaveLength(1);
    wrapper.unmount(); client.clear();
  });

  it("keeps excluded purchases in history but removes them from the price chart", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => Response.json({ ...detail,
      purchases: [...detail.purchases, { ...detail.purchases[0], receipt_id: "excluded", exclusion_reason: "receipt" }],
    })));
    const { wrapper, client } = await mountAt("/items/detail?name=Rohl%C3%ADk&currency=CZK");
    expect(wrapper.findAll("tbody tr")).toHaveLength(3);
    expect(wrapper.get(".chart-stub").attributes("data-count")).toBe("2");
    expect(wrapper.get(".excluded-row").text()).toContain("Excluded by receipt");
    wrapper.unmount(); client.clear();
  });

  it("globally excludes directly in the ledger without opening the product", async () => {
    const fetcher = stubApi();
    const { router, wrapper, client } = await mountAt("/items");
    await wrapper.get('tbody [data-exclusion="product"] button').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/items");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/items/exclusion", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "<b>Rohlík</b>", currency: "CZK", excluded: true }) }));
    wrapper.unmount(); client.clear();
  });

  it("loads excluded products and switches back to included products", async () => {
    const fetcher = stubApi();
    const { router, wrapper, client } = await mountAt("/items?exclusion=excluded&offset=30");
    const excludedUrl = fetcher.mock.calls.map(([input]) => new URL(String(input), "http://test")).find((url) => url.pathname === "/api/v1/items/exclusions");
    expect(excludedUrl?.searchParams.get("scope")).toBe("all");
    await wrapper.get('.exclusion-filter [data-tab="included"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({});
    wrapper.unmount(); client.clear();
  });

  it("sends filters and the default sort, renders names as text", async () => {
    const fetcher = stubApi();
    const { wrapper, client } = await mountAt("/items?q=roh&from=2026-03-01&min=1&max=5");
    const params = itemUrls(fetcher).at(-1)?.searchParams;
    expect(params?.get("q")).toBe("roh");
    expect(params?.get("purchased_from")).toBe(new Date(2026, 2, 1).toISOString());
    expect(params?.get("price_min")).toBe("1");
    expect(params?.get("price_max")).toBe("5");
    expect(params?.get("sort")).toBe("receipt_count_desc");
    expect(wrapper.text()).toContain("1 product");
    expect(wrapper.text()).toContain("<b>Rohlík</b>");
    expect(wrapper.find("td b").exists()).toBe(false);
    const link = wrapper.get(".item-name a").attributes("href");
    expect(link).toContain("/items/detail?name=");
    expect(link).toContain("currency=CZK");
    wrapper.unmount(); client.clear();
  });

  it("excludes selected products everywhere through a previewed batch", async () => {
    const products = [{ ...item, is_excluded: false }, { ...item, name: "Mléko", is_excluded: true }, { ...item, name: "Chléb", is_excluded: false }];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/v1/connections")) return Response.json({ items: [] });
      if (url === "/api/v1/changes") return Response.json({ batch_id: null, dry_run: true, replayed: false, state: "state-1",
        changes: [{ changed: true }], receipts: [], items: [], totals: [] });
      return Response.json({ items: products, total: 3, limit: 30, offset: 0 });
    });
    vi.stubGlobal("fetch", fetcher);
    const { router, wrapper, client } = await mountAt("/items");
    const boxes = wrapper.findAll("tbody .select-cell input");
    await boxes[0]?.setValue(true);
    await boxes[1]?.setValue(true);
    expect(router.currentRoute.value.path).toBe("/items");
    expect(wrapper.get(".selection-bar").text()).toContain("2 products selected");
    await wrapper.get('[data-action="exclude-selected"]').trigger("click");
    await flushPromises();
    const preview = fetcher.mock.calls.find(([input]) => String(input) === "/api/v1/changes");
    expect(JSON.parse(String(preview?.[1]?.body))).toEqual({ dry_run: true, operations: [
      { kind: "product_exclusion", name: "<b>Rohlík</b>", currency: "CZK", excluded: true },
      { kind: "product_exclusion", name: "Mléko", currency: "CZK", excluded: true },
    ] });
    await wrapper.get('[data-action="cancel"]').trigger("click");
    await wrapper.get('[data-action="restore-selected"]').trigger("click");
    await flushPromises();
    const restore = fetcher.mock.calls.filter(([input]) => String(input) === "/api/v1/changes").at(-1);
    expect(JSON.parse(String(restore?.[1]?.body)).operations.map((operation: { excluded: boolean }) => operation.excluded)).toEqual([false, false]);
    await wrapper.get('[data-action="cancel"]').trigger("click");
    await router.replace({ query: { q: "x" } });
    await flushPromises();
    expect(wrapper.find(".selection-bar").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("toggles sorting from column headers", async () => {
    stubApi();
    const { router, wrapper, client } = await mountAt("/items");
    const headers = wrapper.findAll("th button");
    await headers[0]?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ sort: "name_asc" });
    await wrapper.findAll("th button")[0]?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ sort: "name_desc" });
    await wrapper.findAll("th button")[1]?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({});
    await wrapper.findAll("th button")[1]?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ sort: "receipt_count_asc" });
    wrapper.unmount(); client.clear();
  });
});

describe("item detail", () => {
  it("globally excludes and restores the exact product and currency", async () => {
    let excluded = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (init?.method === "PATCH") excluded = JSON.parse(String(init.body)).excluded;
      return Response.json({ ...detail, is_excluded: excluded, included_amount: excluded ? "0.00" : "43.00" });
    });
    vi.stubGlobal("fetch", fetcher);
    const { wrapper, client } = await mountAt("/items/detail?name=Rohl%C3%ADk&currency=CZK");
    await wrapper.get('[data-exclusion="product"] button').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/items/exclusion", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Rohlík", currency: "CZK", excluded: true }) }));
    expect(wrapper.get('[data-exclusion="product"] button').text()).toBe("Restore in all purchases");
    expect(wrapper.get(".included-total").text()).toContain("0.00");
    await wrapper.get('[data-exclusion="product"] button').trigger("click");
    await flushPromises();
    expect(wrapper.get(".included-total").text()).toContain("43.00");
    wrapper.unmount(); client.clear();
  });

  it("shows price summary and links purchases to receipts", async () => {
    stubApi();
    const { wrapper, client } = await mountAt("/items/detail?name=Roh%C3%ADk&currency=CZK");
    expect(wrapper.get("h1").text()).toBe("<b>Rohlík</b>");
    expect(wrapper.text().replace(/\s/g, " ")).toContain("CZK 43.00");
    expect(wrapper.find(".chart-stub").exists()).toBe(true);
    expect(wrapper.get('a[href="/receipts/receipt-2"]').text()).toContain("3 Mar 2026");
    wrapper.unmount(); client.clear();
  });

  it("pages the purchase table while the chart keeps every purchase", async () => {
    const purchases = Array.from({ length: 45 }, (_, index) => ({ ...detail.purchases[0], receipt_id: `receipt-${index}` }));
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(async () => Response.json({ ...detail, purchases })));
    const { wrapper, client } = await mountAt("/items/detail?name=Roh%C3%ADk&currency=CZK");
    expect(wrapper.findAll("tbody tr")).toHaveLength(20);
    expect(wrapper.get(".pagination span").text()).toBe("1–20 of 45");
    expect(wrapper.get(".chart-stub").attributes("data-count")).toBe("45");
    const next = wrapper.findAll(".pagination button").find((button) => button.text() === "Next");
    await next?.trigger("click");
    await next?.trigger("click");
    expect(wrapper.findAll("tbody tr")).toHaveLength(5);
    expect(wrapper.get("tbody a").attributes("href")).toBe("/receipts/receipt-40");
    wrapper.unmount(); client.clear();
  });

  it("explains a missing product", async () => {
    stubApi();
    const { wrapper, client } = await mountAt("/items/detail?name=Nic&currency=EUR");
    expect(wrapper.text()).toContain("This product is unavailable or no longer exists.");
    wrapper.unmount(); client.clear();
  });
});
