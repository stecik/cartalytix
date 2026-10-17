import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConnectionsView from "./views/ConnectionsView.vue";
import { createAppI18n } from "./i18n";

const account = {
  id: "account-1", provider: "lidl", country: "CZ", name: "Family",
  status: "connected", sync_interval_minutes: null, last_synced_at: null,
  next_sync_at: null, latest_sync: null,
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
    const add = wrapper.findAll("button").find((button) => button.text() === "Add Lidl account");
    expect(add).toBeDefined();
    await add!.trigger("click");
    await wrapper.get('input[name="connection-name"]').setValue("My Lidl");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections", expect.objectContaining({ method: "POST", body: JSON.stringify({ name: "My Lidl", sync_interval_minutes: null }) }));
    const albert = wrapper.findAll(".provider-list li").find((row) => row.text().includes("Albert"));
    expect(albert?.get("button").attributes("disabled")).toBeDefined();
    wrapper.unmount(); client.clear();
  });

  it("keeps multiple Lidl accounts distinct and disables sync until linked", async () => {
    const { wrapper, client } = render([account, { ...account, id: "account-2", name: "Partner", status: "not_connected" }]);
    await flushPromises();
    const rows = wrapper.findAll(".connection-list > li");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.text()).toContain("Family");
    expect(rows[1]?.text()).toContain("Partner");
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
    expect(wrapper.text()).toContain("Import in progress");
    expect(wrapper.text()).toContain("Imported: 2");
    items[0] = { ...account, latest_sync: { ...run, status: "completed" } };
    await vi.advanceTimersByTimeAsync(3001);
    await flushPromises();
    expect(wrapper.text()).toContain("Sync completed");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["receipts"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview"] });
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
    expect(wrapper.text()).toContain("The store returned incomplete receipt history.");
    expect(wrapper.text()).not.toContain("history_incomplete");
    wrapper.unmount(); client.clear();
  });

  it("translates structured API failures when starting a sync", async () => {
    const { wrapper, client, fetcher } = render([account]);
    await flushPromises();
    fetcher.mockResolvedValueOnce(Response.json({ error: { code: "connection_busy", message: "private diagnostic" } }, { status: 409 }));
    const button = wrapper.findAll("button").find((item) => item.text() === "Sync now");
    await button!.trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("A sync is already running for this account.");
    expect(wrapper.text()).not.toContain("private diagnostic");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/sync", expect.objectContaining({ method: "POST", body: JSON.stringify({ refresh_existing: false }) }));
    wrapper.unmount(); client.clear();
  });

  it("allows retrying a transient error connection", async () => {
    const { wrapper, client } = render([{ ...account, status: "error" }]);
    await flushPromises();
    const button = wrapper.findAll("button").find((item) => item.text() === "Sync now");
    expect(button).toBeDefined();
    expect(button!.attributes("disabled")).toBeUndefined();
    wrapper.unmount(); client.clear();
  });

  it("shows a local authorization command bound to this connection without requesting store secrets", async () => {
    const { wrapper, client } = render([{ ...account, status: "not_connected" }]);
    await flushPromises();
    const button = wrapper.findAll("button").find((item) => item.text() === "Link with Chrome");
    expect(button).toBeDefined();
    await button!.trigger("click");
    const command = wrapper.get(".link-command").text();
    expect(command).toContain("cartalytix link-lidl");
    expect(command).toContain("--connection account-1");
    expect(command).toContain(`--server ${window.location.origin}`);
    expect(wrapper.find('input[type="password"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Unofficial Lidl API");
    wrapper.unmount(); client.clear();
  });
});
