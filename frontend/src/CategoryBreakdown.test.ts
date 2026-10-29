import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import CategoryBreakdown from "./components/CategoryBreakdown.vue";
import { createAppI18n } from "./i18n";
import type { CategoryStats } from "./api";

interface PieOption {
  tooltip: { formatter: (params: { name: string; value: number }) => string };
  series: Array<{ data: Array<{ name: string; value: number; itemStyle: { color: string } }> }>;
}

const chartStub = { name: "ChartProbe", props: ["option"], emits: ["click"], template: "<div />" };
const COLORS: Record<string, string> = {
  accent: "rgb(1, 1, 1)", "accent-hover": "rgb(2, 2, 2)", "chart-muted": "rgb(3, 3, 3)", focus: "rgb(4, 4, 4)",
  "text-muted": "rgb(5, 5, 5)", border: "rgb(6, 6, 6)", "bg-page": "rgb(7, 7, 7)", "bg-subtle": "rgb(8, 8, 8)",
  "bg-raised": "rgb(9, 9, 9)", text: "rgb(10, 10, 10)", "chart-pointer": "rgba(1, 1, 1, 0.05)",
  "chart-area": "rgba(1, 1, 1, 0.06)", "shadow-accent": "rgba(1, 1, 1, 0.12)",
  "chart-uncategorized": "rgb(15, 0, 0)",
  ...Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8].map((slot) => [`chart-category-${slot}`, `rgb(${10 + slot}, 0, 0)`])),
};

beforeEach(() => {
  for (const [name, value] of Object.entries(COLORS)) document.documentElement.style.setProperty(`--${name}`, value);
});
afterEach(() => document.documentElement.removeAttribute("style"));

function category(id: string, name: string, purchases: number, amount: string): CategoryStats {
  return { category_id: id, name, purchase_count: purchases, amount };
}

const CATEGORIES: CategoryStats[] = [
  category("a", "<b>Pečivo</b>", 10, "400.00"),
  category("b", "Nápoje", 4, "200.00"),
  category("c", "Maso", 2, "150.00"),
  category("d", "Ovoce", 6, "100.00"),
  category("e", "Drogerie", 1, "80.00"),
  category("f", "Sladkosti", 3, "20.00"),
  { category_id: null, name: null, purchase_count: 4, amount: "50.00" },
];

async function render(categories: CategoryStats[], slices: number) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/", component: { template: "<div />" } },
    { path: "/categories", name: "categories", component: { template: "<div />" } },
  ] });
  await router.push("/");
  const wrapper = mount(CategoryBreakdown, {
    props: { categories, currency: "CZK", slices },
    global: { plugins: [router, createAppI18n("en")], stubs: { echarts: chartStub } },
  });
  const charts = wrapper.findAllComponents(chartStub);
  const options = charts.map((chart) => chart.props("option") as PieOption);
  return { wrapper, router, charts, options };
}

function slices(option: PieOption | undefined): Array<[string, number, string]> {
  return (option?.series[0]?.data ?? []).map((slice) => [slice.name, slice.value, slice.itemStyle.color]);
}

describe("category breakdown", () => {
  it("colors every category that fits and lists them all with links", async () => {
    const { wrapper, options } = await render(CATEGORIES, 9);
    expect(slices(options[1])).toEqual([
      ["<b>Pečivo</b>", 400, "rgb(11, 0, 0)"],
      ["Nápoje", 200, "rgb(12, 0, 0)"],
      ["Maso", 150, "rgb(13, 0, 0)"],
      ["Ovoce", 100, "rgb(14, 0, 0)"],
      ["Drogerie", 80, "rgb(15, 0, 0)"],
      ["Sladkosti", 20, "rgb(16, 0, 0)"],
      ["Uncategorized", 50, "rgb(15, 0, 0)"],
    ]);
    expect(options[0]?.series[0]?.data.map((slice) => slice.value)).toEqual([10, 4, 2, 6, 1, 3, 4]);
    expect(options[0]?.tooltip.formatter({ name: "<b>Pečivo</b>", value: 10 })).toContain("&#60;b&#62;Pečivo");
    expect(wrapper.text()).toContain("30×");
    const rows = wrapper.findAll("tbody tr").map((row) =>
      row.findAll("th, td").map((cell) => cell.text().replace(/\s+/g, " ")).join(" | "));
    expect(rows[0]).toBe("<b>Pečivo</b> | 10× 33% | CZK 400.00 40%");
    expect(rows[6]).toBe("Uncategorized | 4× 13% | CZK 50.00 5%");
    expect(wrapper.find("tbody b").exists()).toBe(false);
    const links = wrapper.findAll("tbody a").map((link) => link.attributes("href"));
    expect(links[0]).toBe("/categories?category=a&name=%3Cb%3EPe%C4%8Divo%3C/b%3E");
    expect(links[6]).toBe("/categories?category=uncategorized");
    expect(wrapper.get(".category-breakdown").classes()).toContain("large");
  });

  it("folds categories past the segment setting and opens a clicked segment", async () => {
    const { wrapper, router, charts, options } = await render(CATEGORIES, 5);
    expect(slices(options[1]).map(([name, , color]) => [name, color])).toEqual([
      ["<b>Pečivo</b>", "rgb(11, 0, 0)"],
      ["Nápoje", "rgb(12, 0, 0)"],
      ["Maso", "rgb(13, 0, 0)"],
      ["Ovoce", "rgb(14, 0, 0)"],
      ["Other (2)", "rgb(3, 3, 3)"],
      ["Uncategorized", "rgb(15, 0, 0)"],
    ]);
    expect(wrapper.findAll("tbody tr")).toHaveLength(7);
    charts[0]?.vm.$emit("click", { dataIndex: 1 });
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ category: "b", name: "Nápoje" });
  });

  it("keeps few categories compact beside the table", async () => {
    const { wrapper } = await render(CATEGORIES.slice(0, 3), 9);
    expect(wrapper.get(".category-breakdown").classes()).not.toContain("large");
  });
});
