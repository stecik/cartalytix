import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import OAuthConsentView from "./views/OAuthConsentView.vue";
import { returnPath } from "./auth";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

async function render(fetch: typeof globalThis.fetch, query = "?request=pending-request-token") {
  vi.stubGlobal("fetch", fetch);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/oauth/consent", component: OAuthConsentView }],
  });
  await router.push(`/oauth/consent${query}`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(OAuthConsentView, {
    global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
  });
  await flushPromises();
  return wrapper;
}

describe("MCP consent", () => {
  it("shows the requesting client and returns to it after approval", async () => {
    const assign = vi.fn<(url: string) => void>();
    vi.stubGlobal("location", { ...window.location, assign });
    const fetch = vi.fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(Response.json({ client_name: "ChatGPT", redirect_host: "chatgpt.com", scopes: ["cartalytix:read"] }))
      .mockResolvedValueOnce(Response.json({ redirect_url: "https://chatgpt.com/connector/oauth/x?code=c&state=s" }));
    const wrapper = await render(fetch);
    expect(wrapper.get("h1").text()).toBe("Connect ChatGPT to Cartalytix");
    expect(wrapper.text()).toContain("chatgpt.com");
    const switches = wrapper.findAll<HTMLInputElement>('input[role="switch"]');
    expect(switches.map((input) => input.element.checked)).toEqual([false, true, false, false]);
    expect(switches[1]?.element.disabled).toBe(true);
    await wrapper.get('input[aria-label="Calculator"]').setValue(true);
    await wrapper.get("button.primary").trigger("click");
    await flushPromises();
    const [path, init] = fetch.mock.calls[1] ?? [];
    expect(path).toBe("/api/v1/mcp/authorizations/pending-request-token");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ approve: true, scopes: ["cartalytix:read", "cartalytix:calculator"] }));
    expect(assign).toHaveBeenCalledWith("https://chatgpt.com/connector/oauth/x?code=c&state=s");
  });

  it("enables and disables every optional permission at once", async () => {
    const wrapper = await render(vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      Response.json({ client_name: "Codex", redirect_host: "127.0.0.1", scopes: ["cartalytix:read"] }),
    ));
    const all = wrapper.get<HTMLInputElement>('input[aria-label="Allow everything"]');
    await all.setValue(true);
    expect(wrapper.findAll<HTMLInputElement>('input[role="switch"]').every((input) => input.element.checked)).toBe(true);
    await all.setValue(false);
    expect(wrapper.findAll<HTMLInputElement>('input[role="switch"]').map((input) => input.element.checked)).toEqual([false, true, false, false]);
  });

  it("explains an expired or reused request", async () => {
    const wrapper = await render(vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(null, { status: 404 })));
    expect(wrapper.get('[role="alert"]').text()).toContain("expired or was already used");
    expect(wrapper.find("button.primary").exists()).toBe(false);
  });

  it("only returns to in-app paths after sign-in", () => {
    expect(returnPath("/oauth/consent?request=x")).toBe("/oauth/consent?request=x");
    for (const value of ["//evil.example", "/\\evil.example", "https://evil.example", undefined]) {
      expect(returnPath(value)).toBe("/");
    }
  });
});
