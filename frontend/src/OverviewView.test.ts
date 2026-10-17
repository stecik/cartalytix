import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { h } from "vue";
import type { SetupContext } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OverviewView from "./views/OverviewView.vue";
import { createAppI18n } from "./i18n";
import type { Stats } from "./api";

vi.mock("./components/MonthlySpendingChart.vue", () => ({
  __esModule: true,
  default: {
    props: ["months", "currency", "selected"],
    emits: ["select"],
    setup: (props: { months: unknown[] }, { emit }: SetupContext<["select"]>) => () =>
      h("button", { class: "chart-stub", onClick: () => emit("select", "2026-09") },
        `${props.months.length} months`),
  },
}));
vi.mock("./components/ShoppingHeatmap.vue", () => ({
  __esModule: true,
  default: { props: ["weeklyHours"], setup: () => () => h("div", { class: "heatmap-stub" }) },
}));

const stats: Stats = {
  period: "12m", month: null, currency: "CZK",
  totals: [
    { currency: "CZK", amount: "1200.00", receipt_count: 10, average: "120.00", previous_amount: "1000.00", previous_receipt_count: 10, discount_amount: "85.50" },
    { currency: "EUR", amount: "2.50", receipt_count: 1, average: "2.50", previous_amount: "0.00", previous_receipt_count: 0, discount_amount: "0.00" },
  ],
  months: [{ month: "2026-09", amount: "700.00", receipt_count: 6 }, { month: "2026-10", amount: "500.00", receipt_count: 4 }],
  merchants: [{ merchant: "Lidl", amount: "1200.00", receipt_count: 10 }],
  frequent_items: [{ name: "<b>Rohlík</b>", receipt_count: 8, amount: "24.00" }],
  top_spend_items: [{ name: "Víno", receipt_count: 2, amount: "400.00" }],
  weekly_hours: Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0)),
};
const overview = { receipt_count: 11, connection_count: 1, totals: [], latest_receipts: [] };

afterEach(() => { vi.unstubAllGlobals(); });

async function mountOverview() {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) =>
    Response.json(String(input).startsWith("/api/v1/overview/stats") ? stats : overview));
  vi.stubGlobal("fetch", fetcher);
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/", component: OverviewView }, { path: "/receipts", component: { template: "<div />" } },
  ] });
  await router.push("/");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(OverviewView, { global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  await flushPromises();
  return { wrapper, fetcher, client };
}

function statsUrls(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): string[] {
  return fetcher.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/stats"));
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
    expect(wrapper.get(".chart-stub").text()).toBe("2 months");
    wrapper.unmount(); client.clear();
  });

  it("switches period and drills into a selected month", async () => {
    const { wrapper, fetcher, client } = await mountOverview();
    await wrapper.findAll(".period-switch button").find((button) => button.text() === "This year")?.trigger("click");
    await flushPromises();
    expect(statsUrls(fetcher)).toContain("/api/v1/overview/stats?period=year");
    await wrapper.get(".chart-stub").trigger("click");
    await flushPromises();
    expect(statsUrls(fetcher)).toContain("/api/v1/overview/stats?period=year&month=2026-09");
    expect(wrapper.get(".month-filter").text()).toContain("September 2026");
    expect(wrapper.text()).toContain("vs. previous month");
    await wrapper.get(".month-filter button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".month-filter").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });
});
