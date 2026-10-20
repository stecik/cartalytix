import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createAppI18n } from "./i18n";
import CategoriesView from "./views/CategoriesView.vue";

const CategoryPicker = { name: "CategoryPicker" };

const categories = [{ id: "food", name: "Food", item_count: 2 }];
const product = { connection_id: "store-1", connection_name: "Lidl", name: "Milk", currency: "CZK", receipt_count: 3, total_amount: "90.00", category_id: null, category_name: null, assignment_source: null };
const products = [product, { ...product, connection_id: "store-2", connection_name: "Billa", currency: "EUR", category_id: "food", category_name: "Food", assignment_source: "mcp" }];
const mounted: Array<{ wrapper: VueWrapper; client: QueryClient }> = [];

function api(productsOnPage = products) {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/category-items/assign")) {
      const body = JSON.parse(String(init?.body)) as { items: typeof products; category_id: string | null };
      return Response.json({ matched: body.items.length, changed: body.items.length, protected: 0, dry_run: false, previous_items: body.items.map((item) => ({ connection_id: item.connection_id, name: item.name, currency: item.currency, category_id: productsOnPage.find((product) => product.connection_id === item.connection_id)?.category_id ?? null })) });
    }
    if (url.endsWith("/category-items/restore")) return Response.json({ matched: 2, changed: 2, protected: 0, dry_run: false });
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    if (init?.method === "POST" || init?.method === "PATCH") return Response.json({ id: "food", name: JSON.parse(String(init.body)).name, item_count: 2 });
    if (url.startsWith("/api/v1/categories/page")) return Response.json({ items: categories, total: 21, limit: 10, offset: Number(new URL(url, "http://localhost").searchParams.get("offset")) });
    return Response.json({ items: productsOnPage, next_cursor: url.includes("cursor=") ? null : "next-page" });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = mount(CategoriesView, { attachTo: document.body, global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  mounted.push({ wrapper, client });
  await flushPromises();
  return wrapper;
}

function bodyCalls(fetcher: ReturnType<typeof api>, method: string) {
  return fetcher.mock.calls.filter(([, init]) => init?.method === method).map(([url, init]) => ({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null }));
}
function dialogButton(text: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("dialog button")].find((button) => button.textContent?.trim() === text);
  expect(button).toBeDefined();
  return button!;
}
afterEach(() => { for (const { wrapper, client } of mounted.splice(0)) { wrapper.unmount(); client.clear(); } vi.unstubAllGlobals(); });

describe("categories editor", () => {
  it("shows separate products for different store accounts and currencies", async () => {
    api(); const wrapper = await render();
    expect(wrapper.findAll("tbody tr")).toHaveLength(2);
    expect(wrapper.text()).toContain("Lidl"); expect(wrapper.text()).toContain("Billa");
    expect(wrapper.text()).toContain("CZK"); expect(wrapper.text()).toContain("EUR");
    expect(wrapper.findAll('.category-product-context').map((context) => context.text())).toEqual(["Lidl · CZK", "Billa · EUR"]);
  });
  it("creates and renames categories with trimmed names", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.get('[data-action="create-category"]').trigger("click");
    const input = document.body.querySelector<HTMLInputElement>("dialog input")!;
    input.value = " Drinks "; input.dispatchEvent(new Event("input", { bubbles: true }));
    document.body.querySelector("dialog form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flushPromises();
    expect(bodyCalls(fetcher, "POST")[0]).toEqual({ url: "/api/v1/categories", body: { name: "Drinks" } });
    await wrapper.get('[data-action="rename-category"]').trigger("click");
    const edit = document.body.querySelector<HTMLInputElement>("dialog input")!;
    edit.value = " Groceries "; edit.dispatchEvent(new Event("input", { bubbles: true }));
    document.body.querySelector("dialog form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await flushPromises();
    expect(bodyCalls(fetcher, "PATCH")[0]).toEqual({ url: "/api/v1/categories/food", body: { name: "Groceries" } });
  });
  it("deletes categories only after confirmation without deleting products", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.get('[data-action="delete-category"]').trigger("click");
    expect(bodyCalls(fetcher, "DELETE")).toEqual([]);
    expect(document.body.querySelector("dialog")?.textContent).toContain("Purchases are kept");
    dialogButton("Delete category").click(); await flushPromises();
    expect(bodyCalls(fetcher, "DELETE")).toEqual([{ url: "/api/v1/categories/food", body: null }]);
    expect(wrapper.findAll("tbody tr")).toHaveLength(2);
  });
  it("assigns a single product immediately from its inline picker without a preview", async () => {
    const fetcher = api(); const wrapper = await render();
    wrapper.findAllComponents(CategoryPicker)[0]!.vm.$emit("choose", "food"); await flushPromises();
    expect(bodyCalls(fetcher, "POST")).toEqual([{ url: "/api/v1/category-items/assign", body: { items: [{ connection_id: "store-1", name: "Milk", currency: "CZK" }], category_id: "food", dry_run: false } }]);
    expect(document.body.querySelector("dialog")).toBeNull();
    expect(wrapper.find('[data-action="undo-assignment"]').exists()).toBe(true);
  });
  it("shows one batch picker for selected products and immediately assigns those identities", async () => {
    const fetcher = api(); const wrapper = await render();
    expect(wrapper.find('.category-batch').exists()).toBe(false);
    await wrapper.findAll('tbody input[type="checkbox"]')[1]!.setValue(true);
    const batch = wrapper.get('.category-batch');
    expect(batch.findAllComponents(CategoryPicker)).toHaveLength(1);
    expect(batch.text()).toContain("1 product selected");
    batch.getComponent(CategoryPicker).vm.$emit("choose", "food"); await flushPromises();
    expect(bodyCalls(fetcher, "POST")[0]?.body).toEqual({ items: [{ connection_id: "store-2", name: "Milk", currency: "EUR" }], category_id: "food", dry_run: false });
    expect(wrapper.find('.category-batch').exists()).toBe(false);
  });
  it("assigns all 50 products on the page with one selection and one request", async () => {
    const fetcher = api();
    fetcher.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("categories/page")) return Response.json({ items: categories, total: 1, limit: 10, offset: 0 });
      if (init?.method === "POST") return Response.json({ matched: 50, changed: 50, protected: 0, dry_run: false, previous_items: [] });
      return Response.json({ items: Array.from({ length: 50 }, (_, index) => ({ ...product, name: `Product ${index}` })), next_cursor: null });
    });
    const wrapper = await render();
    await wrapper.get('thead input[type="checkbox"]').setValue(true);
    wrapper.get('.category-batch').getComponent(CategoryPicker).vm.$emit("choose", "food"); await flushPromises();
    expect(bodyCalls(fetcher, "POST")).toHaveLength(1);
    expect(bodyCalls(fetcher, "POST")[0]?.body?.items).toHaveLength(50);
  });
  it("assigns exactly four checked products without changing unchecked rows", async () => {
    const fetcher = api(Array.from({ length: 6 }, (_, index) => ({ ...product, name: "Product " + index })));
    const wrapper = await render();
    for (const checkbox of wrapper.findAll('tbody input[type="checkbox"]').slice(0, 4)) await checkbox.setValue(true);
    expect(wrapper.get('.category-batch').text()).toContain("4 products selected");
    wrapper.get('.category-batch').getComponent(CategoryPicker).vm.$emit("choose", "food"); await flushPromises();
    expect(bodyCalls(fetcher, "POST")[0]?.body?.items).toEqual([
      { connection_id: "store-1", name: "Product 0", currency: "CZK" },
      { connection_id: "store-1", name: "Product 1", currency: "CZK" },
      { connection_id: "store-1", name: "Product 2", currency: "CZK" },
      { connection_id: "store-1", name: "Product 3", currency: "CZK" },
    ]);
  });
  it("removes a row category with × and undo restores authoritative heterogeneous server categories", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.get('[data-action="remove-item-category"]').trigger("click"); await flushPromises();
    expect(bodyCalls(fetcher, "POST")[0]?.body).toEqual({ items: [{ connection_id: "store-2", name: "Milk", currency: "EUR" }], category_id: null, dry_run: false });
    await wrapper.get('thead input[type="checkbox"]').setValue(true);
    fetcher.mockImplementationOnce(async () => Response.json({ matched: 2, changed: 2, protected: 0, dry_run: false, previous_items: [{ connection_id: "store-1", name: "Milk", currency: "CZK", category_id: "other" }, { connection_id: "store-2", name: "Milk", currency: "EUR", category_id: null }] }));
    wrapper.get('.category-batch').getComponent(CategoryPicker).vm.$emit("choose", "food"); await flushPromises();
    await wrapper.get('[data-action="undo-assignment"]').trigger("click"); await flushPromises();
    expect(bodyCalls(fetcher, "POST").at(-1)).toEqual({ url: "/api/v1/category-items/restore", body: { items: [{ connection_id: "store-1", name: "Milk", currency: "CZK", category_id: "other" }, { connection_id: "store-2", name: "Milk", currency: "EUR", category_id: null }], expected_category_id: "food" } });
    expect(wrapper.find('[data-action="undo-assignment"]').exists()).toBe(false);
  });
  it("retains selection after assignment failure and undo snapshot after a conflict", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.get('thead input[type="checkbox"]').setValue(true);
    fetcher.mockImplementationOnce(async () => Response.json({ detail: "failed" }, { status: 500 }));
    wrapper.get('.category-batch').getComponent(CategoryPicker).vm.$emit("choose", null); await flushPromises();
    expect(wrapper.findAll('tbody input:checked')).toHaveLength(2);
    expect(wrapper.find('[role="alert"]').exists()).toBe(true);
    wrapper.get('.category-batch').getComponent(CategoryPicker).vm.$emit("choose", "food"); await flushPromises();
    fetcher.mockImplementationOnce(async () => Response.json({ detail: "conflict" }, { status: 409 }));
    await wrapper.get('[data-action="undo-assignment"]').trigger("click"); await flushPromises();
    expect(wrapper.find('[data-action="undo-assignment"]').exists()).toBe(true);
    expect(wrapper.get('[role="alert"]').text()).toContain("Could not undo");
  });
  it("filters uncategorized products and searches without sending stale cursor or selection", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.findAll('tbody input[type="checkbox"]')[0]!.setValue(true);
    await wrapper.get('[data-action="next-page"]').trigger("click"); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toContain("cursor=next-page");
    await wrapper.get('[data-filter="uncategorized"]').trigger("click"); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toContain("uncategorized=true");
    expect(String(fetcher.mock.calls.at(-1)?.[0])).not.toContain("cursor=");
    expect(wrapper.findAll('tbody input:checked')).toHaveLength(0);
    await wrapper.get('input[data-search="products"]').setValue(" Milk ");
    await new Promise((resolve) => setTimeout(resolve, 350)); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toContain("q=Milk");
  });
  it("searches categories in pages of ten while product filters follow sidebar clicks", async () => {
    const fetcher = api(); const wrapper = await render();
    expect(String(fetcher.mock.calls[0]?.[0])).toContain("/categories/page?limit=10&offset=0");
    await wrapper.get('[data-action="next-categories"]').trigger("click"); await flushPromises();
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("offset=10"))).toBe(true);
    await wrapper.get('input[data-search="categories"]').setValue(" Food ");
    await new Promise((resolve) => setTimeout(resolve, 350)); await flushPromises();
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/categories/page?limit=10&offset=0&q=Food"))).toBe(true);
    await wrapper.get('[data-filter="food"]').trigger("click"); await flushPromises();
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toContain("category_id=food");
    await wrapper.findAllComponents(CategoryPicker)[0]!.get('button').trigger("click"); await flushPromises();
    const pickerCalls = fetcher.mock.calls.filter(([url]) => String(url).includes("categories/page"));
    expect(String(pickerCalls.at(-1)?.[0])).toBe("/api/v1/categories/page?limit=10&offset=0");
  });
  it("starts mobile category browsing collapsed and collapses again after choosing a filter", async () => {
    const fetcher = api(); const wrapper = await render();
    const toggle = wrapper.get('[data-action="browse-categories"]');
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.text()).toContain("All categories");
    expect(wrapper.get('.category-browser').classes()).not.toContain("expanded");
    expect(toggle.attributes("aria-controls")).toBe(wrapper.get('.category-browser').attributes("id"));
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    expect(wrapper.get('.category-browser').classes()).toContain("expanded");
    await wrapper.get('[data-filter="food"]').trigger("click"); await flushPromises();
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.text()).toContain("Food");
    expect(wrapper.get('.category-browser').classes()).not.toContain("expanded");
    expect(String(fetcher.mock.calls.at(-1)?.[0])).toContain("category_id=food");
    await toggle.trigger("click");
    await wrapper.get('[data-filter="uncategorized"]').trigger("click"); await flushPromises();
    expect(toggle.text()).toContain("Uncategorized");
  });
  it("clears stale batch selection when product search changes before debounce", async () => {
    const fetcher = api(); const wrapper = await render();
    await wrapper.get('thead input[type="checkbox"]').setValue(true);
    await wrapper.get('input[data-search="products"]').setValue("new");
    expect(wrapper.find('.category-batch').exists()).toBe(false);
    expect(bodyCalls(fetcher, "POST")).toHaveLength(0);
  });
  it("blocks overlapping row, batch, undo and CRUD mutations until refresh finishes", async () => {
    const fetcher = api(); const wrapper = await render();
    let finish: (response: Response) => void = () => { throw new Error("Assignment request did not start"); };
    fetcher.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
    const pickers = wrapper.findAllComponents(CategoryPicker);
    pickers[0]!.vm.$emit("choose", "food");
    pickers[1]!.vm.$emit("choose", null); await flushPromises();
    expect(bodyCalls(fetcher, "POST")).toHaveLength(1);
    expect(wrapper.get('[data-action="create-category"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('thead input').attributes("disabled")).toBeDefined();
    finish(Response.json({ matched: 1, changed: 1, protected: 0, dry_run: false, previous_items: [{ connection_id: "store-1", name: "Milk", currency: "CZK", category_id: null }] })); await flushPromises();
    expect(wrapper.get('[data-action="create-category"]').attributes("disabled")).toBeUndefined();
  });
});
