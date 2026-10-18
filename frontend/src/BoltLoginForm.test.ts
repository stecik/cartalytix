import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import BoltLoginForm from "./components/BoltLoginForm.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

function render() {
  return mount(BoltLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

describe("Bolt Food account login", () => {
  it("sends the phone, then only the code with the flow handle, and emits the account", async () => {
    const connection = { id: "account-1", provider: "bolt", status: "connected" };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ flow_id: "flow-1" }))
      .mockResolvedValueOnce(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    expect((wrapper.get('input[type="tel"]').element as HTMLInputElement).value).toBe("+420");
    await wrapper.get('input[type="tel"]').setValue("+420 777 123 456");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/providers/bolt/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ phone: "+420777123456" }),
    }));
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("1234");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[autocomplete="one-time-code"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/providers/bolt/connections/account-1/login/verify", expect.objectContaining({
      method: "POST", body: JSON.stringify({ flow_id: "flow-1", code: "1234" }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
  });

  it("returns to the phone step when the verification expired", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ flow_id: "flow-1" }))
      .mockResolvedValueOnce(Response.json({ error: { code: "login_expired" } }, { status: 409 })));
    const wrapper = render();
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("1234");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("The verification expired. Request a new code.");
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true);
  });
});
