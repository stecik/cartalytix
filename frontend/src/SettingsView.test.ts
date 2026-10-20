import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { ref } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsView from "./views/SettingsView.vue";
import { createAppI18n } from "./i18n";
import { sessionOptions } from "./auth";
import { pwaInstallKey } from "./pwaInstall";
import { setTheme, themePreference } from "./theme";
import type { User } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  setTheme("system");
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

function render(available: boolean, user: User | null = null, api?: typeof fetch) {
  vi.stubGlobal("fetch", api ?? vi.fn<typeof fetch>().mockResolvedValue(Response.json(user)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(sessionOptions.queryKey, user);
  const install = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
  const wrapper = mount(SettingsView, { global: {
    plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]],
    provide: { [pwaInstallKey as symbol]: { available: ref(available), install } },
  } });
  return { wrapper, client, install };
}

describe("settings PWA installation", () => {
  it("keeps installation visible without a browser prompt and shows manual instructions", async () => {
    const { wrapper, client, install } = render(false);
    try {
      const button = wrapper.get("button.button.secondary");
      expect(button.text()).toBe("Install app");
      await button.trigger("click");
      expect(wrapper.get('[role="status"]').text()).toContain("Add to Home Screen");
      expect(install).not.toHaveBeenCalled();
    } finally { wrapper.unmount(); client.clear(); }
  });

  it("opens the existing browser installation prompt when available", async () => {
    const { wrapper, client, install } = render(true);
    try {
      await wrapper.get("button.button.secondary").trigger("click");
      await flushPromises();
      expect(install).toHaveBeenCalledOnce();
      expect(wrapper.find('[role="status"]').exists()).toBe(false);
    } finally { wrapper.unmount(); client.clear(); }
  });
});

describe("settings appearance", () => {
  it.each([false, true])("saves a theme immediately and rolls it back on API failure=%s", async (fail) => {
    const user: User = { id: "test", email: "test@example.com", locale: "en", receipt_view: "rendered", theme: "light", dashboard_period: "12m", dashboard_category_slices: 9, is_admin: false, must_change_password: false };
    setTheme("light");
    const { wrapper, client } = render(false, user);
    let finish: (value: Response) => void = () => undefined;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((_input, init) =>
      init?.method === "PATCH" ? new Promise<Response>((resolve) => { finish = resolve; }) : Promise.resolve(Response.json(user)));
    vi.stubGlobal("fetch", fetchMock);
    try {
      await flushPromises();
      await wrapper.get('button[aria-label="Appearance"]').trigger("click");
      const dark = wrapper.findAll('[role="option"]').find((option) => option.text() === "Dark");
      expect(dark).toBeDefined();
      await dark?.trigger("click");
      await flushPromises();
      expect(themePreference.value).toBe("dark");
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/auth/me"), expect.objectContaining({ method: "PATCH", body: JSON.stringify({ theme: "dark" }) }));
      finish(fail ? Response.json({ detail: "Unavailable" }, { status: 503 }) : Response.json({ ...user, theme: "dark" }));
      await flushPromises();
      expect(themePreference.value).toBe(fail ? "light" : "dark");
      expect(localStorage.getItem("cartalytix-theme")).toBe(fail ? "light" : "dark");
      expect(client.getQueryData<User>(sessionOptions.queryKey)?.theme).toBe(fail ? "light" : "dark");
      expect(wrapper.find('[role="alert"]').exists()).toBe(fail);
    } finally { wrapper.unmount(); client.clear(); }
  });
});

describe("settings connected AI apps", () => {
  it("lists MCP grants and revokes one after confirmation", async () => {
    let items = [{ id: "grant-1", client_name: "ChatGPT", created_at: "2026-10-01T10:00:00Z", scopes: ["cartalytix:read"], last_used_at: null }];
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (String(input).endsWith("/mcp/grants/grant-1") && init?.method === "DELETE") {
        items = [];
        return new Response(null, { status: 204 });
      }
      return String(input).endsWith("/mcp/grants") ? Response.json({ items }) : Response.json(null);
    });
    const { wrapper, client } = render(false, null, fetchMock);
    try {
      await flushPromises();
      expect(wrapper.get("#mcp-heading").text()).toBe("Connected AI apps");
      expect(wrapper.text()).toContain("ChatGPT");
      expect(wrapper.text()).toContain("/mcp");
      const revoke = wrapper.findAll("button").find((button) => button.text() === "Revoke access");
      await revoke?.trigger("click");
      await wrapper.get(".app-dialog button.danger").trigger("click");
      await flushPromises();
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/mcp/grants/grant-1", expect.objectContaining({ method: "DELETE" }));
      expect(wrapper.find(".app-dialog").exists()).toBe(false);
      expect(wrapper.text()).toContain("No apps are connected.");
    } finally { wrapper.unmount(); client.clear(); }
  });

  it("saves permission switches and offers revoking when read is turned off", async () => {
    const grant = { id: "grant-1", client_name: "Codex", created_at: "2026-10-01T10:00:00Z", scopes: ["cartalytix:read"], last_used_at: "2026-10-20T10:00:00Z" };
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      if (init?.method === "PATCH") return Response.json({ ...grant, scopes: JSON.parse(String(init.body)).scopes });
      return String(input).endsWith("/mcp/grants") ? Response.json({ items: [grant] }) : Response.json(null);
    });
    const { wrapper, client } = render(false, null, fetchMock);
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("last used 20 Oct 2026");
      await wrapper.get('input[aria-label="Manage categories"]').setValue(true);
      await flushPromises();
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/mcp/grants/grant-1", expect.objectContaining({
        method: "PATCH", body: JSON.stringify({ scopes: ["cartalytix:read", "cartalytix:categories:write"] }),
      }));
      expect(wrapper.get<HTMLInputElement>('input[aria-label="Manage categories"]').element.checked).toBe(true);
      await wrapper.get('input[aria-label="Allow everything"]').setValue(true);
      await flushPromises();
      expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/mcp/grants/grant-1", expect.objectContaining({
        body: JSON.stringify({ scopes: ["cartalytix:read", "cartalytix:categories:write", "cartalytix:calculator"] }),
      }));
      const read = wrapper.get<HTMLInputElement>('input[aria-label="Read data"]');
      await read.setValue(false);
      expect(read.element.checked).toBe(true);
      expect(wrapper.get(".app-dialog").text()).toContain("Codex will immediately lose access");
    } finally { wrapper.unmount(); client.clear(); }
  });
});
