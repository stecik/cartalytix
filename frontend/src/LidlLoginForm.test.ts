import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import LidlLoginForm from "./components/LidlLoginForm.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

function render(locale: "en" | "cs" = "en") {
  return mount(LidlLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n(locale)] },
  });
}

describe("Lidl account login", () => {
  it("submits directly to the connector, clears password immediately and emits the connected account", async () => {
    let complete!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("private-password");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/lidl/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "owner@example.com", password: "private-password" }),
    }));
    const connection = { id: "account-1", status: "connected" };
    complete(Response.json({ step: "completed", connection }));
    await flushPromises();
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(wrapper.html()).not.toContain("private-password");
    expect(wrapper.find("pre").exists()).toBe(false);
    wrapper.unmount();
  });

  it("uses a separate verification step and never resends the password", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ step: "otp", flow_id: "flow-1", method: "email" }))
      .mockResolvedValueOnce(Response.json({ step: "completed", connection: { id: "account-1" } }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render("cs");
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("private-password");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('input[type="password"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("Ověřovací kód");
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("123456");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/providers/lidl/connections/account-1/login/flow-1/verify", expect.objectContaining({
      method: "POST", body: JSON.stringify({ code: "123456" }),
    }));
    expect(wrapper.html()).not.toContain("123456");
    wrapper.unmount();
  });

  it("cancels abandoned MFA and shows localized errors without provider diagnostics", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ step: "otp", flow_id: "flow-1" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("private-password");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    wrapper.unmount();
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/providers/lidl/connections/account-1/login/flow-1", expect.objectContaining({ method: "DELETE" }));

    fetcher.mockResolvedValueOnce(Response.json({ error: { code: "credentials_rejected", message: "private-provider-text" } }, { status: 422 }));
    const failed = render();
    await failed.get('input[type="email"]').setValue("owner@example.com");
    await failed.get('input[type="password"]').setValue("private-password");
    await failed.get("form").trigger("submit");
    await flushPromises();
    expect(failed.get('[role="alert"]').text()).toContain("Lidl rejected");
    expect(failed.html()).not.toContain("private-");
    failed.unmount();
  });
});
