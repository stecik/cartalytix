import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import WoltLoginForm from "./components/WoltLoginForm.vue";
import { createAppI18n } from "./i18n";

afterEach(() => vi.unstubAllGlobals());

const LINK = "https://wolt.com/me/magic_login?email=a%40example.com&token=magic-1";

function render() {
  return mount(WoltLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

describe("Wolt account login", () => {
  it("sends only the pasted link once, clears it and emits the account", async () => {
    const connection = { id: "account-1", provider: "wolt", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    expect(wrapper.find('a[href="https://wolt.com/cs/cze"]').exists()).toBe(true);
    await wrapper.get('input[type="url"]').setValue(` ${LINK} `);
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="url"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/wolt/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ link: LINK }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
  });

  it("explains a rejected link", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code: "magic_link_rejected" } }, { status: 422 }),
    ));
    const wrapper = render();
    await wrapper.get('input[type="url"]').setValue(LINK);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Wolt rejected the link");
    expect(wrapper.emitted("connected")).toBeUndefined();
  });
});
