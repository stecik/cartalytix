import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import RankedBars from "./components/RankedBars.vue";
import type { RankedRow } from "./components/RankedBars.vue";
import { createAppI18n } from "./i18n";

const rows: RankedRow[] = Array.from({ length: 23 }, (_, index) => ({
  key: `store-${index}`,
  label: `Store ${index + 1}`,
  value: 230 - index * 10,
  display: `${230 - index * 10} Kč`,
  detail: "",
}));

describe("ranked bars", () => {
  it("pages long rankings while keeping ranks and one bar scale", async () => {
    const wrapper = mount(RankedBars, { props: { rows, pageSize: 10 }, global: { plugins: [createAppI18n("en")] } });
    expect(wrapper.findAll("li")).toHaveLength(10);
    expect(wrapper.get(".pagination span").text()).toBe("1–10 of 23");
    const [previous, next] = wrapper.findAll(".pagination button");
    expect(previous?.attributes("disabled")).toBeDefined();
    await next?.trigger("click");
    await next?.trigger("click");
    expect(wrapper.findAll("li")).toHaveLength(3);
    expect(wrapper.get(".rank").text()).toBe("21");
    expect(wrapper.get(".ranked-fill").attributes("style")).toContain(`width: ${(30 / 230) * 100}%`);
    expect(next?.attributes("disabled")).toBeDefined();
    await wrapper.setProps({ rows: rows.slice(0, 12) });
    expect(wrapper.get(".rank").text()).toBe("1");
  });

  it("shows every row without pagination when no page size is set", () => {
    const wrapper = mount(RankedBars, { props: { rows }, global: { plugins: [createAppI18n("en")] } });
    expect(wrapper.findAll("li")).toHaveLength(23);
    expect(wrapper.find(".pagination").exists()).toBe(false);
  });
});
