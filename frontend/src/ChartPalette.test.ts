import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import SpendingChart from "./components/SpendingChart.vue";
import PriceHistoryChart from "./components/PriceHistoryChart.vue";
import ShoppingHeatmap from "./components/ShoppingHeatmap.vue";
import { createAppI18n } from "./i18n";
import { setTheme } from "./theme";
import { nextTick } from "vue";

const chartStub = { name: "ChartProbe", props: ["option"], template: "<div />" };
const global = {
  plugins: [createAppI18n("en")],
  stubs: { echarts: chartStub },
};

beforeEach(() => {
  setTheme("light");
  const palette: Record<string, string> = {
    accent: "rgb(11, 22, 33)",
    "accent-hover": "rgb(22, 33, 44)",
    "chart-muted": "rgb(33, 44, 55)",
    focus: "rgb(44, 55, 66)",
    "text-muted": "rgb(55, 66, 77)",
    border: "rgb(66, 77, 88)",
    "bg-page": "rgb(77, 88, 99)",
    "bg-subtle": "rgb(88, 99, 110)",
    "bg-raised": "rgb(99, 110, 121)",
    text: "rgb(110, 121, 132)",
    "chart-pointer": "rgba(11, 22, 33, 0.05)",
    "chart-area": "rgba(11, 22, 33, 0.06)",
    "shadow-accent": "rgba(11, 22, 33, 0.12)",
    "chart-category-1": "rgb(1, 2, 3)",
    "chart-category-2": "rgb(4, 5, 6)",
    "chart-category-3": "rgb(7, 8, 9)",
    "chart-category-4": "rgb(10, 11, 12)",
    "chart-category-5": "rgb(16, 17, 18)",
    "chart-category-6": "rgb(19, 20, 21)",
    "chart-category-7": "rgb(22, 23, 24)",
    "chart-category-8": "rgb(25, 26, 27)",
    "chart-uncategorized": "rgb(13, 14, 15)",
  };
  for (const [name, value] of Object.entries(palette)) {
    document.documentElement.style.setProperty(`--${name}`, value);
  }
});

afterEach(() => {
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
});

describe("chart palette", () => {
  it("recolors mounted charts when appearance changes", async () => {
    const charts = [
      mount(SpendingChart, { global, props: {
        buckets: [{ key: "2026-10", amount: "100", receipt_count: 1, merchants: [] }], granularity: "month", currency: "CZK", selected: null,
      } }),
      mount(PriceHistoryChart, { global, props: { purchases: [], currency: "CZK" } }),
      mount(ShoppingHeatmap, { global, props: { weeklyHours: [[1]] } }),
    ];
    try {
      document.documentElement.style.setProperty("--text-muted", "rgb(200, 210, 220)");
      document.documentElement.style.setProperty("--bg-raised", "rgb(20, 30, 40)");
      setTheme("dark");
      await nextTick();
      for (const chart of charts) {
        const option = chart.getComponent({ name: "ChartProbe" }).props("option");
        expect(option.xAxis.axisLabel.color).toBe("rgb(200, 210, 220)");
        expect(option.tooltip.backgroundColor).toBe("rgb(20, 30, 40)");
      }
    } finally { for (const chart of charts) chart.unmount(); }
  });

  it("uses CSS colors for spending, selection, axes and tooltips", async () => {
    const wrapper = mount(SpendingChart, { global, props: {
      buckets: [{ key: "2026-10", amount: "100", receipt_count: 1, merchants: [] }], granularity: "month",
      currency: "CZK", selected: null,
    } });
    const option = wrapper.getComponent({ name: "ChartProbe" }).props("option");
    expect(option.series[0].data[0].itemStyle.color).toBe("rgb(11, 22, 33)");
    expect(option.series[0].emphasis.itemStyle.color).toBe("rgb(22, 33, 44)");
    expect(option.series[1].lineStyle.color).toBe("rgb(44, 55, 66)");
    expect(option.xAxis.axisLine.lineStyle.color).toBe("rgb(66, 77, 88)");
    expect(option.xAxis.axisLabel.color).toBe("rgb(55, 66, 77)");
    expect(option.tooltip.backgroundColor).toBe("rgb(99, 110, 121)");
    expect(option.tooltip.textStyle.color).toBe("rgb(110, 121, 132)");
    expect(option.tooltip.axisPointer.shadowStyle.color).toBe("rgba(11, 22, 33, 0.05)");
    expect(option.tooltip.extraCssText).toContain("rgba(11, 22, 33, 0.12)");
    await wrapper.setProps({ selected: "2026-09" });
    expect(wrapper.getComponent({ name: "ChartProbe" }).props("option").series[0].data[0].itemStyle.color)
      .toBe("rgb(33, 44, 55)");
    wrapper.unmount();
  });

  it("uses the CSS palette for price areas and heatmap scales", () => {
    const price = mount(PriceHistoryChart, { global, props: { purchases: [], currency: "CZK" } });
    const option = price.getComponent({ name: "ChartProbe" }).props("option");
    expect(option.series[0].areaStyle.color).toBe("rgba(11, 22, 33, 0.06)");
    expect(option.series[0].itemStyle.borderColor).toBe("rgb(77, 88, 99)");
    price.unmount();
    const heatmap = mount(ShoppingHeatmap, { global, props: { weeklyHours: [[1]] } });
    expect(heatmap.getComponent({ name: "ChartProbe" }).props("option").visualMap.inRange.color)
      .toEqual(["rgb(88, 99, 110)", "rgb(44, 55, 66)", "rgb(11, 22, 33)"]);
    heatmap.unmount();
  });
});

