import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReceiptSearch from "./components/ReceiptSearch.vue";
import { createAppI18n } from "./i18n";
import { highlight } from "./search";

const items = [
  { kind: "item", text: "Rohlík tukový", receipt_count: 12 },
  { kind: "merchant", text: "<b>Rohlik</b> shop", receipt_count: 2 },
];

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function setup() {
  vi.useFakeTimers();
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => Response.json({ items }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(ReceiptSearch, {
    props: { modelValue: "", "onUpdate:modelValue": (value: string) => wrapper.setProps({ modelValue: value }) },
    global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
    attachTo: document.body,
  });
  return { wrapper, fetcher, client };
}

async function type(wrapper: ReturnType<typeof setup>["wrapper"], value: string) {
  await wrapper.get("input").setValue(value);
  await vi.advanceTimersByTimeAsync(250);
  await flushPromises();
}

describe("receipt search suggestions", () => {
  it("separates one-off and global exclusion suggestions", async () => {
    const { wrapper, fetcher, client } = setup();
    await wrapper.setProps({ exclusion: "excluded", scope: "item" });
    await wrapper.get("input").trigger("focus");
    await type(wrapper, "rohlik");
    expect(new URL(String(fetcher.mock.calls.at(-1)?.[0]), "http://test").searchParams.get("scope")).toBe("item");
    await wrapper.setProps({ scope: "product" });
    await flushPromises();
    expect(new URL(String(fetcher.mock.calls.at(-1)?.[0]), "http://test").searchParams.get("scope")).toBe("product");
    wrapper.unmount(); client.clear();
  });

  it("searches the selected exclusion scope and refetches when it changes", async () => {
    const { wrapper, fetcher, client } = setup();
    await wrapper.setProps({ exclusion: "excluded" });
    await wrapper.get("input").trigger("focus");
    await type(wrapper, "rohlik");
    expect(new URL(String(fetcher.mock.calls.at(-1)?.[0]), "http://test").searchParams.get("exclusion")).toBe("excluded");
    await wrapper.setProps({ exclusion: "all" });
    await flushPromises();
    expect(new URL(String(fetcher.mock.calls.at(-1)?.[0]), "http://test").searchParams.get("exclusion")).toBe("all");
    wrapper.unmount(); client.clear();
  });

  it("waits for three characters and debounces requests", async () => {
    const { wrapper, fetcher, client } = setup();
    await wrapper.get("input").trigger("focus");
    await type(wrapper, "ro");
    expect(fetcher).not.toHaveBeenCalled();
    await wrapper.get("input").setValue("roh");
    await wrapper.get("input").setValue("rohl");
    await wrapper.get("input").setValue("rohli");
    await vi.advanceTimersByTimeAsync(250);
    await flushPromises();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0]?.[0])).toBe("/api/v1/receipts/suggestions?q=rohli");
    expect(wrapper.get("input").attributes("aria-expanded")).toBe("true");
    expect(wrapper.findAll("[role=option]")).toHaveLength(2);
    expect(wrapper.get("mark").text()).toBe("Rohlí");
    expect(wrapper.text()).toContain("<b>Rohlik</b> shop");
    expect(wrapper.find("li b").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("selects a suggestion with keyboard or mouse", async () => {
    const { wrapper, client } = setup();
    await wrapper.get("input").trigger("focus");
    await type(wrapper, "rohlik");
    const input = wrapper.get("input");
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "ArrowDown" });
    expect(input.attributes("aria-activedescendant")).toBe(wrapper.findAll("[role=option]")[1]?.attributes("id"));
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")).toEqual([["<b>Rohlik</b> shop"]]);
    expect(input.attributes("aria-expanded")).toBe("false");
    await type(wrapper, "rohlik t");
    await wrapper.findAll("[role=option]")[0]?.trigger("mousedown");
    expect(wrapper.emitted("select")?.at(-1)).toEqual(["Rohlík tukový"]);
    wrapper.unmount(); client.clear();
  });

  it("closes on escape", async () => {
    const { wrapper, client } = setup();
    await wrapper.get("input").trigger("focus");
    await type(wrapper, "rohlik");
    await wrapper.get("input").trigger("keydown", { key: "Escape" });
    expect(wrapper.get("input").attributes("aria-expanded")).toBe("false");
    wrapper.unmount(); client.clear();
  });
});

describe("accent-insensitive highlight", () => {
  it("maps folded matches back to the original characters", () => {
    expect(highlight("Pražská šunka", "sunk")).toEqual([
      { text: "Pražská ", match: false },
      { text: "šunk", match: true },
      { text: "a", match: false },
    ]);
    expect(highlight("Kornbageta", "xyz")).toEqual([{ text: "Kornbageta", match: false }]);
  });
});
