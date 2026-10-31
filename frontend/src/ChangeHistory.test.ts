import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChangeHistoryList from "./components/ChangeHistoryList.vue";
import ChangesView from "./views/ChangesView.vue";
import type { ChangeHistoryBatch, ChangeHistoryEntry } from "./api";
import { createAppI18n } from "./i18n";

function entry(id: string, patch: Partial<ChangeHistoryEntry>): ChangeHistoryEntry {
  return { id, kind: "item_exclusion", receipt_id: "receipt-1", item_id: `item-${id}`, name: "Burger", currency: "CZK", before: false, after: true, undone_by_id: null, ...patch };
}

const totals = [{ currency: "CZK", before_amount: "200.00", after_amount: "150.00", difference: "-50.00" }];
const batches: ChangeHistoryBatch[] = [
  { id: "batch-1", created_at: "2026-10-30T12:00:00Z", source: "mcp", client_name: "Claude", undo_of_id: null, totals, entries: [
    entry("e1", {}),
    entry("e2", { kind: "item_included_quantity", before: null, after: "1", undone_by_id: "batch-9" }),
    entry("e3", { kind: "item_included_quantity", name: "Fries", before: "2", after: null }),
  ] },
  { id: "batch-2", created_at: "2026-10-29T12:00:00Z", source: "user", client_name: null, undo_of_id: "batch-0", totals: [], entries: [
    entry("e4", { kind: "receipt_exclusion", item_id: null, name: "Lidl", undone_by_id: "batch-8" }),
  ] },
  { id: "batch-3", created_at: "2026-10-28T12:00:00Z", source: "mcp", client_name: null, undo_of_id: null, totals: [], entries: [
    entry("e5", { kind: "product_exclusion", receipt_id: null, item_id: null, name: "Rohlík" }),
    entry("e6", { name: "Milk", before: true, after: false }),
  ] },
];

function undoOutput(dryRun: boolean) {
  return { batch_id: dryRun ? null : "batch-10", dry_run: dryRun, replayed: false, state: "state-1",
    changes: [{ index: 0, kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-e1", name: "Burger", currency: "CZK", before: true, after: false, changed: true }],
    receipts: [{ currency: "CZK", before_amount: "150.00", after_amount: "200.00", difference: "50.00", receipt_id: "receipt-1", merchant: "Lidl", purchased_at: "2026-10-17T10:00:00Z" }],
    items: [{ currency: "CZK", before_amount: "0.00", after_amount: "50.00", difference: "50.00", receipt_id: "receipt-1", item_id: "item-e1", name: "Burger" }],
    totals: [{ currency: "CZK", before_amount: "150.00", after_amount: "200.00", difference: "50.00" }],
  };
}

function bodies(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): Array<Record<string, unknown>> {
  return fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
}

async function mountWith(component: typeof ChangesView | typeof ChangeHistoryList, client: QueryClient, props: Record<string, unknown> = {}) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/changes", name: "changes", component: ChangesView },
    { path: "/receipts/:id", name: "receipt-detail", component: { template: "<div />" } },
  ] });
  await router.push("/changes");
  return mount(component, { props, global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("change history", () => {
  it("reads recorded changes as sentences with their source and undone state", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ items: batches, total: 3, limit: 30, offset: 0 }));
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = await mountWith(ChangesView, client);
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/changes?limit=30&offset=0", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    const [first, second, third] = wrapper.findAll(".change-batch");
    expect(first?.text()).toContain("AI assistant (Claude)");
    expect(first?.get(".change-batch-totals").text().replace(/\s/g, " ")).toBe("-CZK 50.00");
    expect(first?.findAll(".change-sentence").map((item) => item.text())).toEqual(["Excluded line Burger", "Counted 1 of line Burger", "Counted whole line Fries"]);
    expect(first?.get(".change-undone").text()).toContain("Undone");
    expect(first?.get(".change-sentence").attributes("href")).toBe("/receipts/receipt-1");
    // Only entries still in effect can be undone one by one.
    expect(first?.findAll('[data-action="undo-entry"]')).toHaveLength(2);
    expect(first?.find('[data-action="undo-batch"]').exists()).toBe(true);
    expect(second?.text()).toContain("You");
    expect(second?.text()).toContain("Undo of an earlier change");
    expect(second?.text()).toContain("Excluded receipt Lidl");
    expect(second?.find('[data-action="undo-batch"]').exists()).toBe(false);
    expect(second?.find('[data-action="undo-entry"]').exists()).toBe(false);
    expect(third?.text()).toContain("AI assistant");
    expect(third?.findAll(".change-sentence").map((item) => item.text())).toEqual(["Excluded product Rohlík in all purchases", "Restored line Milk"]);
    wrapper.unmount(); client.clear();
  });

  it("previews an undo as a dry run and applies it once with an idempotency key", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      Response.json(undoOutput(JSON.parse(String(init?.body)).dry_run)));
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient();
    client.setQueryData(["changes", "/changes?limit=30&offset=0"], { marker: "old" });
    const wrapper = await mountWith(ChangeHistoryList, client, { batches });
    await wrapper.get('[data-action="undo-batch"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/changes/batch-1/undo", expect.objectContaining({ method: "POST" }));
    // One entry is already undone, so the whole-batch undo names the others.
    expect(bodies(fetcher)[0]).toEqual({ entry_ids: ["e1", "e3"], dry_run: true });
    expect(wrapper.get(".change-difference").text().replace(/\s/g, " ")).toBe("+CZK 50.00");
    await wrapper.get('[data-action="apply-changes"]').trigger("click");
    await flushPromises();
    const confirm = bodies(fetcher)[1];
    expect(confirm).toMatchObject({ entry_ids: ["e1", "e3"], dry_run: false });
    expect(confirm?.idempotency_key).toMatch(/^[\da-f]{32}$/);
    expect(confirm).not.toHaveProperty("expected_state");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".change-list").exists()).toBe(false);
    expect(client.getQueryState(["changes", "/changes?limit=30&offset=0"])?.isInvalidated).toBe(true);
    wrapper.unmount(); client.clear();
  });

  it("lists conflicting later changes instead of undoing over them", async () => {
    const conflicts = [{ entry_id: "e5", name: "Rohlík", expected: true, current: false }];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "undo_conflict", params: { conflicts } } }, { status: 409 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = await mountWith(ChangeHistoryList, new QueryClient(), { batches });
    await wrapper.findAll(".change-batch")[2]?.get('[data-action="undo-entry"]').trigger("click");
    await flushPromises();
    expect(bodies(fetcher)[0]).toEqual({ entry_ids: ["e5"], dry_run: true });
    expect(wrapper.get('[role="alert"]').text()).toContain("nothing was undone");
    const list = wrapper.get(".conflict-list");
    expect(list.text()).toContain("Rohlík");
    expect(list.text()).toContain("Changed to excluded, now included");
    expect(wrapper.find('[data-action="apply-changes"]').exists()).toBe(false);
    expect(wrapper.find('[data-action="retry-preview"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
