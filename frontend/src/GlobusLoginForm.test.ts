import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import GlobusLoginForm from "./components/GlobusLoginForm.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

function render() {
  return mount(GlobusLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

describe("Globus account login", () => {
  it("sends only the pasted cookie once, clears it and emits the account", async () => {
    const connection = { id: "account-1", provider: "globus", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    expect(wrapper.find('a[href="https://www.globus.cz/"]').exists()).toBe(true);
    await wrapper.get('input[type="password"]').setValue(" cookie-1 ");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/globus/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ cookie: "cookie-1" }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
  });

  it("explains a rejected cookie", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code: "session_cookie_rejected" } }, { status: 422 }),
    ));
    const wrapper = render();
    await wrapper.get('input[type="password"]').setValue("cookie-1");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Globus did not accept the session");
    expect(wrapper.emitted("connected")).toBeUndefined();
  });
});
