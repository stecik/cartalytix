import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import FoodoraTokenForm from "./components/FoodoraTokenForm.vue";
import { createAppI18n } from "./i18n";
import { sourceName } from "./names";

afterEach(() => vi.unstubAllGlobals());

function render() {
  return mount(FoodoraTokenForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

async function fill(wrapper: ReturnType<typeof render>): Promise<void> {
  await wrapper.get('input[type="password"]').setValue(" private-token ");
  await wrapper.get("form").trigger("submit");
}

describe("foodora token linking", () => {
  it("posts the token once, clears the input and emits the account", async () => {
    const connection = { id: "account-1", provider: "foodora", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(connection));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/foodora/connections/account-1/authorize", expect.objectContaining({
      method: "POST", body: JSON.stringify({ access_token: "private-token" }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(wrapper.html()).not.toContain("private-token");
  });

  it("explains rejected tokens", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code: "auth_required" } }, { status: 422 }),
    ));
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("foodora rejected this token or it has expired. Sign in on foodora.cz again and copy a fresh value.");
  });

  it("translates foodora fee and discount slugs", () => {
    const { t } = createAppI18n("cs").global;
    expect(sourceName("delivery_fee", t)).toBe("Doprava");
    expect(sourceName("rider_tip", t)).toBe("Spropitné kurýrovi");
    expect(sourceName("voucher", t)).toBe("Voucher");
  });
});
