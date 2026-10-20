import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { h } from "vue";
import type { SetupContext } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OverviewView from "./views/OverviewView.vue";
import AppDateInput from "./components/AppDateInput.vue";
import { createAppI18n } from "./i18n";
import type { Stats, User } from "./api";

vi.mock("./components/SpendingChart.vue", () => ({
  __esModule: true,
  default: {
    props: ["buckets", "granularity", "currency", "selected"],
    emits: ["select"],
    setup: (props: { buckets: unknown[]; granularity: string }, { emit }: SetupContext<["select"]>) => () =>
      h("button", { class: "chart-stub", onClick: () => emit("select", "2026-09") },
        `${props.buckets.length} ${props.granularity}`),
  },
}));
vi.mock("./components/ShoppingHeatmap.vue", () => ({
  __esModule: true,
  default: { props: ["weeklyHours"], setup: () => () => h("div", { class: "heatmap-stub" }) },
}));
vi.mock("./components/CategoryBreakdown.vue", () => ({
  __esModule: true,
  default: {
    props: ["categories", "currency"],
    setup: (props: { categories: unknown[] }) => () => h("div", { class: "categories-stub" }, String(props.categories.length)),
  },
}));

const stats: Stats = {
  period: "12m", bucket: null, granularity: "month", currency: "CZK",
  totals: [
    { currency: "CZK", amount: "1200.00", receipt_count: 10, average: "120.00", previous_amount: "1000.00", previous_receipt_count: 10, discount_amount: "85.50" },
    { currency: "EUR", amount: "2.50", receipt_count: 1, average: "2.50", previous_amount: "0.00", previous_receipt_count: 0, discount_amount: "0.00" },
  ],
  buckets: [
    { key: "2026-09", amount: "700.00", receipt_count: 6, merchants: [{ merchant: "Lidl", amount: "700.00", receipt_count: 6 }] },
    { key: "2026-10", amount: "500.00", receipt_count: 4, merchants: [] },
  ],
  merchants: [{ merchant: "Lidl", amount: "1200.00", receipt_count: 10 }],
  frequent_items: [{ name: "<b>Rohlík</b>", receipt_count: 8, amount: "24.00" }],
  top_spend_items: [{ name: "Víno", receipt_count: 2, amount: "400.00" }],
  categories: [],
  weekly_hours: Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0)),
};
const overview = { receipt_count: 11, connection_count: 1, totals: [], latest_receipts: [] };
const user: User = { id: "u", email: "u@example.com", locale: "en", receipt_view: "rendered", theme: "system", dashboard_period: "12m", dashboard_category_slices: 9, is_admin: false, must_change_password: false };

afterEach(() => { vi.unstubAllGlobals(); });

async function mountOverview(dashboardPeriod: User["dashboard_period"] = "12m") {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) =>
    Response.json(String(input).startsWith("/api/v1/overview/stats") ? stats : overview));
  vi.stubGlobal("fetch", fetcher);
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/", component: OverviewView }, { path: "/receipts", name: "receipts", component: { template: "<div />" } },
    { path: "/items/detail", name: "item-detail", component: { template: "<div />" } },
  ] });
  await router.push("/");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["session"], { ...user, dashboard_period: dashboardPeriod });
  const wrapper = mount(OverviewView, { attachTo: document.body, global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { wrapper, fetcher, client };
}

function statsUrls(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): string[] {
  return fetcher.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/stats"));
}

async function choosePeriod(wrapper: Awaited<ReturnType<typeof mountOverview>>["wrapper"], label: string): Promise<void> {
  await wrapper.get(".period-select .app-select-trigger").trigger("click");
  await wrapper.findAll(".period-select [role=option]").find((option) => option.text() === label)?.trigger("click");
  await flushPromises();
}

describe("overview dashboard", () => {
  it("shows period KPIs, trends and rankings without interpreting HTML", async () => {
    const { wrapper, fetcher, client } = await mountOverview();
    expect(statsUrls(fetcher)).toEqual(["/api/v1/overview/stats?period=12m"]);
    const text = wrapper.text().replace(/\s/g, " ");
    expect(text).toContain("CZK 1,200.00");
    expect(text).toContain("+20 %");
    expect(text).toContain("Also €2.50");
    expect(text).toContain("CZK 85.50");
    expect(text).toContain("<b>Rohlík</b>");
    expect(wrapper.find(".ranked-bars b").exists()).toBe(false);
    expect(text).toContain("Víno");
    expect(wrapper.get(".chart-stub").text()).toBe("2 month");
    expect(wrapper.find(".categories-stub").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("shows category shares once something is categorized", async () => {
    stats.categories = [
      { category_id: "c1", name: "Pečivo", purchase_count: 5, amount: "50.00" },
      { category_id: null, name: null, purchase_count: 3, amount: "30.00" },
    ];
    try {
      const { wrapper, client } = await mountOverview();
      expect(wrapper.get(".categories-stub").text()).toBe("2");
      wrapper.unmount(); client.clear();
    } finally { stats.categories = []; }
  });

  it("opens with the user's default period", async () => {
    const { wrapper, fetcher, client } = await mountOverview("30d");
    expect(statsUrls(fetcher)).toEqual(["/api/v1/overview/stats?period=30d"]);
    expect(wrapper.get(".period-select .app-select-trigger").text()).toBe("Last 30 days");
    wrapper.unmount(); client.clear();
  });

  it("switches period and drills into a selected month", async () => {
    const { wrapper, fetcher, client } = await mountOverview();
    await choosePeriod(wrapper, "This year");
    expect(statsUrls(fetcher)).toContain("/api/v1/overview/stats?period=year");
    await wrapper.get(".chart-stub").trigger("click");
    await flushPromises();
    expect(statsUrls(fetcher)).toContain("/api/v1/overview/stats?period=year&bucket=2026-09");
    expect(wrapper.get(".bucket-filter").text()).toContain("September 2026");
    expect(wrapper.text()).toContain("vs. previous month");
    await wrapper.get(".bucket-filter button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".bucket-filter").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("queries a custom date range", async () => {
    const { wrapper, fetcher, client } = await mountOverview();
    await choosePeriod(wrapper, "Custom");
    const [from, to] = wrapper.findAllComponents(AppDateInput);
    expect(from?.props("modelValue")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    from?.vm.$emit("update:modelValue", "2026-03-05");
    to?.vm.$emit("update:modelValue", "2026-03-07");
    await flushPromises();
    expect(statsUrls(fetcher)).toContain("/api/v1/overview/stats?period=custom&date_from=2026-03-05&date_to=2026-03-07");
    wrapper.unmount(); client.clear();
  });
});
