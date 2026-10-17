import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import FermatoLoginForm from "./components/FermatoLoginForm.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

function render() {
  return mount(FermatoLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

describe("Fermato account login", () => {
  it("posts credentials once, clears the password and emits the connected account", async () => {
    const connection = { id: "account-1", provider: "fermato", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    expect(wrapper.text()).toContain("stored encrypted");
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("private-password");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/fermato/connections/account-1/login", expect.objectContaining({
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
    expect(wrapper.get('[role="alert"]').text()).toBe("Fermato rejected the sign-in. Check your email and password.");
    expect(wrapper.emitted("connected")).toBeUndefined();
  });
});
