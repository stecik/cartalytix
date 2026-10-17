import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConnectionsView from "./views/ConnectionsView.vue";
import { createAppI18n } from "./i18n";

const account = {
  id: "account-1", provider: "lidl", country: "CZ", name: "Family",
  status: "connected", sync_interval_minutes: null, last_synced_at: null,
  next_sync_at: null, latest_sync: null, receipt_count: 7,
};

function render(items: object[] = []) {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/providers")) {
      return Response.json({ items: [
        { code: "lidl", name: "Lidl", status: "available" },
        { code: "albert", name: "Albert", status: "not_implemented" },
      ] });
    }
    if (init?.method === "POST" && path.endsWith("/connections")) {
      return Response.json({ ...account, ...JSON.parse(String(init.body)), status: "not_connected" }, { status: 201 });
    }
    return Response.json({ items });
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = mount(ConnectionsView, { global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
  return { wrapper, fetcher, client };
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("Lidl connections", () => {
  it("creates a named account with manual scheduling and leaves other stores unavailable", async () => {
    const { wrapper, fetcher, client } = render();
    await flushPromises();
    const add = wrapper.findAll("button").find((button) => button.text() === "Add account");
    expect(add).toBeDefined();
    await add!.trigger("click");
    await wrapper.get('input[name="connection-name"]').setValue("My Lidl");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections", expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "My Lidl", sync_interval_minutes: null }) }));
    const albert = wrapper.findAll(".upcoming-store").find((row) => row.text().includes("Albert"));
    expect(albert?.text()).toContain("Coming soon");
    expect(albert?.find("button").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("keeps multiple Lidl accounts distinct and disables sync until linked", async () => {
    const { wrapper, client } = render([account, { ...account, id: "account-2", name: "Partner", status: "not_connected" }]);
    await flushPromises();
    const rows = wrapper.findAll(".account-list > li");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.text()).toContain("Family");
    expect(rows[1]?.text()).toContain("Partner");
    await rows[1]!.get(".account-toggle").trigger("click");
    const sync = rows[1]?.findAll("button").find((button) => button.text() === "Sync now");
    expect(sync?.attributes("disabled")).toBeDefined();
    expect(rows[1]?.text()).toContain("Not connected");
    wrapper.unmount(); client.clear();
  });

  it("polls active imports, refreshes receipts on completion, then stops polling", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    const run = { id: "run-1", connection_id: account.id, status: "running", error_code: null,
      created_at: "2026-10-17T10:00:00Z", started_at: "2026-10-17T10:00:00Z", finished_at: null,
      receipts_seen: 3, receipts_imported: 2, receipts_skipped: 1, pages_count: 1 };
    const items = [{ ...account, latest_sync: run }];
    const { wrapper, client, fetcher } = render(items);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    expect(wrapper.text()).toContain("Import in progress");
    expect(wrapper.text()).toContain("Imported: 2");
    items[0] = { ...account, latest_sync: { ...run, status: "completed" } };
    await vi.advanceTimersByTimeAsync(3001);
    await flushPromises();
    expect(wrapper.text()).toContain("Sync completed");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["receipts"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview-stats"] });
    const requests = fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(9000);
    await flushPromises();
    expect(fetcher.mock.calls.length).toBe(requests);
    wrapper.unmount(); client.clear();
  });

  it("localizes failed imports without displaying raw provider errors", async () => {
    const run = { id: "run-1", connection_id: account.id, status: "failed", error_code: "history_incomplete",
      created_at: "2026-10-17T10:00:00Z", started_at: null, finished_at: "2026-10-17T10:01:00Z",
      receipts_seen: 3, receipts_imported: 2, receipts_skipped: 1, pages_count: 1 };
    const { wrapper, client } = render([{ ...account, latest_sync: run }]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    expect(wrapper.text()).toContain("The store returned incomplete receipt history.");
    expect(wrapper.text()).not.toContain("history_incomplete");
    wrapper.unmount(); client.clear();
  });

  it("translates structured API failures when starting a sync", async () => {
    const { wrapper, client, fetcher } = render([account]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    fetcher.mockResolvedValueOnce(Response.json({ error: { code: "connection_busy", message: "private diagnostic" } }, { status: 409 }));
    const button = wrapper.findAll("button").find((item) => item.text() === "Sync now");
    await button!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("A synchronization or sign-in is in progress. Wait for it to finish and try again.");
    expect(wrapper.text()).not.toContain("private diagnostic");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/sync", expect.objectContaining({ method: "POST", body: JSON.stringify({ refresh_existing: false }) }));
    wrapper.unmount(); client.clear();
  });

  it("allows retrying a transient error connection", async () => {
    const { wrapper, client } = render([{ ...account, status: "error" }]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    const button = wrapper.findAll("button").find((item) => item.text() === "Sync now");
    expect(button).toBeDefined();
    expect(button!.attributes("disabled")).toBeUndefined();
    wrapper.unmount(); client.clear();
  });

  it("opens the connector login form for this account without terminal instructions", async () => {
    const { wrapper, client } = render([{ ...account, status: "not_connected" }]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    const button = wrapper.findAll("button").find((item) => item.text() === "Connect account");
    expect(button).toBeDefined();
    await button!.trigger("click");
    expect(wrapper.find(".link-command").exists()).toBe(false);
    expect(wrapper.get('input[type="password"]').attributes("id")).toBe("lidl-password-account-1");
    expect(wrapper.text()).toContain("unofficial Lidl API");
    wrapper.unmount(); client.clear();
  });

  it("confirms data deletion, pauses automatically by default and refreshes receipt views", async () => {
    const { wrapper, client, fetcher } = render([account]);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.get('[role="tab"][data-tab="manage"]').trigger("click");
    await wrapper.get('[data-action="clear-data"]').trigger("click");
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith("/clear-data"))).toBe(false);
    const dialog = wrapper.get("dialog");
    expect(dialog.text()).toContain("7 receipts");
    expect((dialog.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true);
    fetcher.mockResolvedValueOnce(Response.json({ ...account, receipt_count: 0 }));
    await dialog.get('[data-action="confirm"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/clear-data", expect.objectContaining({ method: "POST", body: JSON.stringify({ pause_sync: true }) }));
    expect(wrapper.find("dialog").exists()).toBe(false);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["receipts"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview-stats"] });
    wrapper.unmount(); client.clear();
  });

  it("disconnects without deleting receipts and removes an account only after confirmation", async () => {
    const { wrapper, client, fetcher } = render([account]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.get('[role="tab"][data-tab="manage"]').trigger("click");
    fetcher.mockResolvedValueOnce(Response.json({ ...account, status: "disconnected" }));
    await wrapper.get('[data-action="disconnect"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/disconnect", expect.objectContaining({ method: "POST" }));
    expect(client.getQueryData<{ items: typeof account[] }>(["connections"])?.items[0]?.receipt_count).toBe(7);
    await wrapper.get('[data-action="remove"]').trigger("click");
    await wrapper.get("dialog").get('[data-action="cancel"]').trigger("click");
    expect(wrapper.find("dialog").exists()).toBe(false);
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    await wrapper.get('[data-action="remove"]').trigger("click");
    fetcher.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await wrapper.get("dialog").get('[data-action="confirm"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1", expect.objectContaining({ method: "DELETE" }));
    expect(wrapper.find(".account-toggle").exists()).toBe(false);
    wrapper.unmount(); client.clear();
  });

  it("saves automatic scheduling and loads history only when requested", async () => {
    const { wrapper, client, fetcher } = render([account]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    expect(fetcher.mock.calls.some(([path]) => String(path).includes("/runs"))).toBe(false);
    fetcher.mockResolvedValueOnce(Response.json({ ...account, sync_interval_minutes: 1440 }));
    await wrapper.get('input[role="switch"]').setValue(true);
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ sync_interval_minutes: 1440 }) }));
    fetcher.mockResolvedValueOnce(Response.json({ ...account, sync_interval_minutes: 60 }));
    await wrapper.get("select").setValue(60);
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ sync_interval_minutes: 60 }) }));
    fetcher.mockResolvedValueOnce(Response.json({ items: [{ id: "run", created_at: "2026-10-17T10:00:00Z", finished_at: "2026-10-17T10:01:00Z", status: "completed", receipts_imported: 42, receipts_skipped: 7 }] }));
    await wrapper.get('[data-tab="sync"]').trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    expect(wrapper.get('[data-tab="history"]').attributes("aria-selected")).toBe("true");
    expect(wrapper.text()).toContain("Imported: 42");
    expect(wrapper.text()).toContain("Skipped: 7");
    wrapper.unmount(); client.clear();
  });

  it("blocks destructive actions during an active sync", async () => {
    const run = { id: "run", connection_id: account.id, status: "running", error_code: null, created_at: "2026-10-17T10:00:00Z", receipts_imported: 1, receipts_skipped: 0, receipts_seen: 1, pages_count: 1 };
    const { wrapper, client } = render([{ ...account, latest_sync: run }]);
    await flushPromises();
    expect(wrapper.get('[data-action="remove"]').attributes("disabled")).toBeDefined();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.get('[data-tab="manage"]').trigger("click");
    expect(wrapper.get('[data-action="clear-data"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-action="disconnect"]').attributes("disabled")).toBeDefined();
    wrapper.unmount(); client.clear();
  });

  it("can retain automatic scheduling when clearing data and keeps failures in the confirmation", async () => {
    const { wrapper, client, fetcher } = render([account]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.get('[data-tab="manage"]').trigger("click");
    await wrapper.get('[data-action="clear-data"]').trigger("click");
    await wrapper.get("dialog").get('input[type="checkbox"]').setValue(false);
    fetcher.mockResolvedValueOnce(Response.json({ error: { code: "connection_busy", message: "internal detail" } }, { status: 409 }));
    await wrapper.get("dialog").get('[data-action="confirm"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/clear-data", expect.objectContaining({ method: "POST", body: JSON.stringify({ pause_sync: false }) }));
    expect(wrapper.get("dialog").get('[role="alert"]').text()).toContain("Wait for it to finish");
    expect(wrapper.text()).not.toContain("internal detail");
    wrapper.unmount(); client.clear();
  });

  it("can clear leftover source data with zero receipts and a queued sync", async () => {
    const { wrapper, client, fetcher } = render([{ ...account, receipt_count: 0, latest_sync: { status: "pending" } }]);
    await flushPromises();
    expect(wrapper.get('[data-action="remove"]').attributes("disabled")).toBeUndefined();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.get('[data-tab="manage"]').trigger("click");
    expect(wrapper.get('[data-action="clear-data"]').attributes("disabled")).toBeUndefined();
    await wrapper.get('[data-action="clear-data"]').trigger("click");
    fetcher.mockResolvedValueOnce(Response.json({ ...account, receipt_count: 0 }));
    await wrapper.get("dialog").get('[data-action="confirm"]').trigger("click");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/clear-data", expect.objectContaining({ method: "POST" }));
    wrapper.unmount(); client.clear();
  });

  it("preserves an in-flight sign-in when its account row is collapsed", async () => {
    const { wrapper, client, fetcher } = render([{ ...account, status: "not_connected" }]);
    await flushPromises();
    await wrapper.get(".account-toggle").trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "Connect account")!.trigger("click");
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("synthetic-password");
    let complete!: (response: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
    await wrapper.get(".lidl-login-form").trigger("submit");
    await wrapper.get(".account-toggle").trigger("click");
    complete(Response.json({ step: "completed", connection: account }));
    await flushPromises();
    expect(client.getQueryData<{ items: typeof account[] }>(["connections"])?.items[0]?.status).toBe("connected");
    expect(wrapper.get(".account-toggle").text()).toContain("Connected");
    wrapper.unmount(); client.clear();
  });
});
