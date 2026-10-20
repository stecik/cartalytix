import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAppI18n } from "./i18n";
import CategoryPicker from "./components/CategoryPicker.vue";

const mounted: Array<{ wrapper: VueWrapper; client: QueryClient }> = [];
function api() {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const params = new URL(String(input), "http://localhost").searchParams;
    const offset = Number(params.get("offset"));
    const q = params.get("q");
    return Response.json({ items: q === "Missing" ? [] : q ? [{ id: "drink", name: "Drinks", item_count: 1 }] : Array.from({ length: 10 }, (_, index) => ({ id: "category-" + (offset + index), name: "Category " + (offset + index), item_count: 1 })), total: q === "Missing" ? 0 : q ? 1 : 21, limit: 10, offset });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function render(disabled = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(CategoryPicker, { props: { categoryId: "category-0", display: "Category 0", label: "Change category", disabled }, attachTo: document.body, global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  mounted.push({ wrapper, client });
  return wrapper;
}
function input(): HTMLInputElement { return document.body.querySelector<HTMLInputElement>('[role="combobox"]')!; }
function option(index: number): HTMLElement { return document.body.querySelectorAll<HTMLElement>('[role="option"]')[index]!; }
function button(label: string): HTMLButtonElement { return [...document.body.querySelectorAll<HTMLButtonElement>(".category-picker-panel button")].find((node) => node.textContent?.trim() === label)!; }
async function key(value: string): Promise<void> { input().dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true })); await flushPromises(); }
afterEach(() => { for (const { wrapper, client } of mounted.splice(0)) { wrapper.unmount(); client.clear(); } vi.unstubAllGlobals(); });

describe("category picker", () => {
  it("loads lazily, selects with mouse and restores trigger focus", async () => {
    const fetcher = api(); const wrapper = render();
    expect(fetcher).not.toHaveBeenCalled();
    const trigger = wrapper.get("button");
    await trigger.trigger("click"); await flushPromises();
    expect(input()).toBe(document.activeElement);
    expect(input().getAttribute("aria-controls")).toBe(document.body.querySelector('[role="listbox"]')?.id);
    expect(option(1).getAttribute("aria-selected")).toBe("true");
    option(2).click(); await flushPromises();
    expect(wrapper.emitted("choose")).toEqual([["category-1"]]);
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
    expect(document.activeElement).toBe(trigger.element);
  });
  it("navigates and scrolls options with arrow keys while preserving native text cursor keys", async () => {
    api(); const wrapper = render(); const trigger = wrapper.get("button");
    await trigger.trigger("keydown", { key: "ArrowDown" }); await flushPromises();
    const scroll = vi.fn(); option(10).scrollIntoView = scroll;
    for (let step = 0; step < 10; step += 1) await key("ArrowDown");
    expect(input().getAttribute("aria-activedescendant")).toBe(option(10).id);
    expect(scroll).toHaveBeenCalledWith({ block: "nearest" });
    for (const cursorKey of ["Home", "End"]) {
      const event = new KeyboardEvent("keydown", { key: cursorKey, bubbles: true, cancelable: true });
      input().dispatchEvent(event); await flushPromises();
      expect(event.defaultPrevented).toBe(false);
      expect(input().getAttribute("aria-activedescendant")).toBe(option(10).id);
    }
    for (let step = 0; step < 9; step += 1) await key("ArrowUp");
    await key("Enter");
    expect(wrapper.emitted("choose")).toEqual([["category-0"]]);
    await trigger.trigger("click"); await flushPromises();
    await key("Escape");
    expect(document.activeElement).toBe(trigger.element);
    expect(wrapper.emitted("choose")).toHaveLength(1);
  });
  it("pages by ten and debounces search back to the first page", async () => {
    const fetcher = api(); const wrapper = render();
    await wrapper.get("button").trigger("click"); await flushPromises();
    button("Next").click(); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toBe("/api/v1/categories/page?limit=10&offset=10");
    expect(option(1).textContent).toBe("Category 10");
    input().value = " Drinks "; input().dispatchEvent(new Event("input", { bubbles: true })); await flushPromises();
    await key("Enter");
    expect(wrapper.emitted("choose")).toBeUndefined();
    await new Promise((resolve) => setTimeout(resolve, 350)); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toBe("/api/v1/categories/page?limit=10&offset=0&q=Drinks");
    expect(option(0).textContent).toBe("Drinks");
    await key("Enter");
    expect(wrapper.emitted("choose")).toEqual([["drink"]]);
  });
  it("does not remove a category when Enter is pressed on an unmatched search", async () => {
    api(); const wrapper = render();
    await wrapper.get("button").trigger("click"); await flushPromises();
    input().value = "Missing"; input().dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 350)); await flushPromises();
    expect(document.body.querySelectorAll('[role="option"]')).toHaveLength(0);
    expect(input().hasAttribute("aria-activedescendant")).toBe(false);
    await key("Enter");
    expect(wrapper.emitted("choose")).toBeUndefined();
  });
  it("closes outside and blocks a disabled picker including an already open list", async () => {
    const fetcher = api(); const wrapper = render(true);
    await wrapper.get("button").trigger("keydown", { key: "Enter" }); await flushPromises();
    expect(fetcher).not.toHaveBeenCalled();
    await wrapper.setProps({ disabled: false });
    await wrapper.get("button").trigger("click"); await flushPromises();
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })); await flushPromises();
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
    await wrapper.get("button").trigger("click"); await flushPromises();
    await wrapper.setProps({ disabled: true });
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
    expect(wrapper.emitted("choose")).toBeUndefined();
  });
  it("keeps pagination reachable with Tab and closes when focus leaves the picker", async () => {
    api(); const wrapper = render();
    await wrapper.get("button").trigger("click"); await flushPromises();
    await key("Tab");
    expect(document.body.querySelector('[role="listbox"]')).not.toBeNull();
    const next = button("Next"); next.focus(); await flushPromises();
    expect(document.activeElement).toBe(next);
    next.click(); await flushPromises();
    expect(option(1).textContent).toBe("Category 10");
    next.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await flushPromises();
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
    expect(document.activeElement).toBe(wrapper.get("button").element);
    await wrapper.get("button").trigger("click"); await flushPromises();
    const outside = document.createElement("button"); document.body.append(outside); outside.focus(); await flushPromises();
    expect(document.body.querySelector('[role="listbox"]')).toBeNull();
    outside.remove();
  });
  it("retries category loading errors without accidentally removing the current category", async () => {
    const fetcher = api(); fetcher.mockImplementationOnce(async () => Response.json({ detail: "failed" }, { status: 500 }));
    const wrapper = render();
    await wrapper.get("button").trigger("click"); await flushPromises();
    await key("Enter");
    expect(wrapper.emitted("choose")).toBeUndefined();
    expect(document.body.querySelector('[role="alert"]')).not.toBeNull();
    button("Try again").click(); await flushPromises();
    option(0).click(); await flushPromises();
    expect(wrapper.emitted("choose")).toEqual([[null]]);
  });
});
