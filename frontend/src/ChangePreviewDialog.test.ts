import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChangePreviewDialog from "./components/ChangePreviewDialog.vue";
import type { ChangeOperation } from "./api";
import { createAppI18n } from "./i18n";

const operations: ChangeOperation[] = [{ kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-1", excluded: true }];

function output(state: string, changed = true) {
  const amounts = { currency: "CZK", before_amount: "20.00", after_amount: "0.00", difference: "-20.00" };
  return { batch_id: null, dry_run: true, replayed: false, state,
    changes: [{ index: 0, kind: "item_exclusion", receipt_id: "receipt-1", item_id: "item-1", name: "Apples", currency: "CZK", before: !changed, after: true, changed }],
    receipts: changed ? [{ ...amounts, receipt_id: "receipt-1", merchant: "Lidl", purchased_at: "2026-10-17T10:00:00Z" }] : [],
    items: changed ? [{ ...amounts, receipt_id: "receipt-1", item_id: "item-1", name: "Apples" }] : [],
    totals: changed ? [{ currency: "CZK", before_amount: "120.00", after_amount: "100.00", difference: "-20.00" }] : [],
  };
}

function bodies(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): Array<Record<string, unknown>> {
  return fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
}

function mountDialog(client = new QueryClient()) {
  return mount(ChangePreviewDialog, {
    props: { title: "Exclude selected", body: { operations } },
    global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
  });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("change preview", () => {
  it("previews with a dry run and applies the previewed state once", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
      Response.json({ ...output("state-1"), dry_run: JSON.parse(String(init?.body)).dry_run }));
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient();
    client.setQueryData(["overview"], { marker: "old" });
    const wrapper = mountDialog(client);
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/changes", expect.objectContaining({ method: "POST" }));
    expect(bodies(fetcher)[0]).toEqual({ operations, dry_run: true });
    expect(wrapper.get(".change-difference").text().replace(/\s/g, " ")).toBe("-CZK 20.00");
    expect(wrapper.get(".change-list").text()).toContain("Apples");
    await wrapper.get('[data-action="apply-changes"]').trigger("click");
    await flushPromises();
    const confirm = bodies(fetcher)[1];
    expect(confirm).toMatchObject({ operations, dry_run: false, expected_state: "state-1" });
    expect(confirm?.idempotency_key).toMatch(/^[\da-f]{32}$/);
    expect(wrapper.emitted("applied")).toHaveLength(1);
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(client.getQueryState(["overview"])?.isInvalidated).toBe(true);
    wrapper.unmount(); client.clear();
  });

  it("re-runs the preview after a conflict and retries with the same key", async () => {
    let previews = 0;
    let confirms = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      if (JSON.parse(String(init?.body)).dry_run) return Response.json(output(`state-${++previews}`));
      if (++confirms === 1) return Response.json({ error: { code: "changes_conflict", params: {} } }, { status: 409 });
      return Response.json({ ...output("state-2"), dry_run: false, batch_id: "batch-1" });
    });
    vi.stubGlobal("fetch", fetcher);
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.get('[data-action="apply-changes"]').trigger("click");
    await flushPromises();
    expect(bodies(fetcher).map((body) => body.dry_run)).toEqual([true, false, true]);
    expect(wrapper.get('[role="alert"]').text()).toContain("Your data changed since the preview");
    expect(wrapper.emitted("applied")).toBeUndefined();
    await wrapper.get('[data-action="apply-changes"]').trigger("click");
    await flushPromises();
    const [, first, , second] = bodies(fetcher);
    expect(first?.expected_state).toBe("state-1");
    expect(second?.expected_state).toBe("state-2");
    expect(second?.idempotency_key).toBe(first?.idempotency_key);
    expect(wrapper.emitted("applied")).toHaveLength(1);
    wrapper.unmount();
  });

  it("disables applying when nothing would change and translates errors", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(Response.json(output("state-1", false))));
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.text()).toContain("Nothing would change");
    expect(wrapper.get('[data-action="apply-changes"]').attributes("disabled")).toBeDefined();
    wrapper.unmount();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: { code: "receipt_item_not_found", params: { operation: 0 } } }, { status: 404 })));
    const failed = mountDialog();
    await flushPromises();
    expect(failed.get('[role="alert"]').text()).toContain("Some selected records no longer exist");
    expect(failed.find('[data-action="retry-preview"]').exists()).toBe(true);
    failed.unmount();
  });
});
