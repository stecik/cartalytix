import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import MixitLoginForm from "./components/MixitLoginForm.vue";
import ConnectionsView from "./views/ConnectionsView.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

function render() {
  return mount(MixitLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

describe("Mixit account login", () => {
  it("posts credentials once, clears the password and emits the connected account", async () => {
    const connection = { id: "account-1", provider: "mixit", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("private-password");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/mixit/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "owner@example.com", password: "private-password" }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(wrapper.html()).not.toContain("private-password");
  });

  it("shows a translated rejection", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code: "credentials_rejected" } }, { status: 422 }),
    ));
    const wrapper = render();
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("wrong");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Mixit rejected the sign-in. Check your email and password.");
    expect(wrapper.emitted("connected")).toBeUndefined();
  });

  it("creates Mixit accounts through the provider endpoint and shows the Mixit form", async () => {
    const account = {
      id: "mixit-1", provider: "mixit", country: "CZ", name: "Mixit",
      status: "not_connected", sync_interval_minutes: null, last_synced_at: null,
      next_sync_at: null, latest_sync: null, receipt_count: 0,
    };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/providers")) return Response.json({ items: [{ code: "mixit", name: "Mixit", status: "available" }] });
      if (init?.method === "POST") return Response.json(account, { status: 201 });
      return Response.json({ items: [] });
    });
    vi.stubGlobal("fetch", fetcher);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const wrapper = mount(ConnectionsView, { global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] } });
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "Add account")!.trigger("click");
    expect(wrapper.text()).toContain("Add Mixit account");
    await wrapper.get('input[name="connection-name"]').setValue("Mixit");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/mixit/connections", expect.objectContaining({ method: "POST" }));
    expect(wrapper.find('input[id="mixit-email-mixit-1"]').exists()).toBe(true);
    wrapper.unmount(); client.clear();
  });
});
