import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceiptsView from "./views/ReceiptsView.vue";
import { createAppI18n } from "./i18n";

const receipt = { id: "receipt-1", connection_id: "account-1", provider: "lidl", external_id: "external-1",
  purchased_at: "2026-10-17T10:00:00Z", merchant: "Lidl Praha", total_amount: "20.00", currency: "CZK" };

function stubApi(items: unknown[] = [receipt]) {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) =>
    String(input).startsWith("/api/v1/connections")
      ? Response.json({ items: [{ id: "account-1", provider: "lidl", name: "My Lidl" }] })
      : String(input).startsWith("/api/v1/receipts/suggestions")
        ? Response.json({ items: [] })
        : Response.json({ items, total: items.length, limit: 30, offset: 0 }));
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function receiptUrls(fetcher: ReturnType<typeof stubApi>): URL[] {
  return fetcher.mock.calls.map(([input]) => new URL(String(input), "http://test"))
    .filter((url) => url.pathname === "/api/v1/receipts");
}

async function mountAt(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/receipts", name: "receipts", component: ReceiptsView },
    { path: "/receipts/:id", name: "receipt-detail", component: { template: "<div />" } },
    { path: "/connections", component: { template: "<div />" } },
  ] });
  await router.push(path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(ReceiptsView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { router, wrapper, client };
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("receipt filters", () => {
  it("sends filters from the URL to the API with local day bounds", async () => {
    const fetcher = stubApi();
    const { wrapper, client } = await mountAt(
      "/receipts?q=milk&from=2026-01-01&to=2026-01-31&connection=account-1&min=10&max=20.5&sort=total_amount_asc&offset=30");
    const params = receiptUrls(fetcher).at(-1)?.searchParams;
    expect(params?.get("q")).toBe("milk");
    expect(params?.get("purchased_from")).toBe(new Date(2026, 0, 1).toISOString());
    expect(params?.get("purchased_to")).toBe(new Date(2026, 1, 1).toISOString());
    expect(params?.get("connection_id")).toBe("account-1");
    expect(params?.get("amount_min")).toBe("10");
    expect(params?.get("amount_max")).toBe("20.5");
    expect(params?.get("sort")).toBe("total_amount_asc");
    expect(params?.get("offset")).toBe("30");
    expect((wrapper.get('input[type="search"]').element as HTMLInputElement).value).toBe("milk");
    const account = wrapper.get(".account-select button");
    expect(account.text()).toContain("My Lidl");
    expect(account.find("img").exists()).toBe(true);
    wrapper.unmount(); client.clear();
  });

  it("toggles sorting from column headers and keeps it in the URL", async () => {
    stubApi();
    const { router, wrapper, client } = await mountAt("/receipts?offset=30");
    const dateHeader = wrapper.get('th[aria-sort="descending"]');
    expect(dateHeader.text()).toContain("Date");
    await dateHeader.get("button").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ sort: "purchased_at_asc" });
    await wrapper.findAll("th button").at(1)?.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ sort: "total_amount_desc" });
    expect(wrapper.get('th[aria-sort="descending"]').text()).toContain("Amount");
    wrapper.unmount(); client.clear();
  });

  it("debounces search, resets the page and clears all filters", async () => {
    vi.useFakeTimers();
    stubApi();
    const { router, wrapper, client } = await mountAt("/receipts?min=5&offset=30");
    await wrapper.get('input[type="search"]').setValue("mléko");
    expect(router.currentRoute.value.query).toEqual({ min: "5", offset: "30" });
    await vi.advanceTimersByTimeAsync(300);
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ min: "5", q: "mléko" });
    await wrapper.get("button.reset-filters").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({});
    expect((wrapper.get('input[type="search"]').element as HTMLInputElement).value).toBe("");
    wrapper.unmount(); client.clear();
  });

  it("explains an empty filtered result instead of suggesting a connection", async () => {
    stubApi([]);
    const { wrapper, client } = await mountAt("/receipts?q=nothing");
    expect(wrapper.text()).toContain("No receipts match these filters.");
    expect(wrapper.find('a[href="/connections"]').exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });
});
