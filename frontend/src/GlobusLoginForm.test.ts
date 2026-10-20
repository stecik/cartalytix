import { flushPromises, mount } from "@vue/test-utils";
import type { VueWrapper } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "./api";
import GlobusLoginForm from "./components/GlobusLoginForm.vue";
import { createAppI18n } from "./i18n";

const wrappers: VueWrapper[] = [];
const connection: Connection = {
  id: "account-1", provider: "globus", name: "Globus", status: "connected", country: "CZ",
  sync_interval_minutes: null, last_synced_at: null, next_sync_at: null, latest_sync: null, receipt_count: 0,
};

afterEach(() => {
  wrappers.forEach((wrapper) => wrapper.unmount());
  wrappers.length = 0;
  vi.unstubAllGlobals();
});

function render(locale: "en" | "cs" = "en"): VueWrapper {
  const wrapper = mount(GlobusLoginForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n(locale)] },
  });
  wrappers.push(wrapper);
  return wrapper;
}

async function enterCredentials(wrapper: VueWrapper, username: string = "owner@example.com"): Promise<void> {
  await wrapper.get('input[autocomplete="username"]').setValue(username);
  await wrapper.get('input[type="password"]').setValue("private-password");
}

describe("Globus account login", () => {
  it.each(["owner@example.com", "2112345678901"])("connects with username %s and clears password before waiting", async (username: string) => {
    let complete!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    const usernameInput = wrapper.get('input[autocomplete="username"]');
    expect(usernameInput.attributes("type")).toBe("text");
    expect(usernameInput.attributes("maxlength")).toBe("254");
    expect(wrapper.get('input[type="password"]').attributes("maxlength")).toBe("1024");
    await enterCredentials(wrapper, username);
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeDefined();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/globus/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ username, password: "private-password" }),
    }));
    complete(Response.json(connection));
    await flushPromises();
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(wrapper.html()).not.toContain("private-password");
    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeUndefined();
  });

  it("ignores duplicate submissions while sign-in is pending", async () => {
    let complete!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await enterCredentials(wrapper);
    await wrapper.get("form").trigger("submit");
    await wrapper.get("form").trigger("submit");
    expect(fetcher).toHaveBeenCalledOnce();
    complete(Response.json(connection));
    await flushPromises();
    expect(wrapper.emitted("connected")).toHaveLength(1);
  });

  it("does not emit a late account after the form is unmounted", async () => {
    let complete!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockImplementation(() => new Promise((resolve) => { complete = resolve; })));
    const wrapper = render();
    await enterCredentials(wrapper);
    await wrapper.get("form").trigger("submit");
    wrapper.unmount();
    complete(Response.json(connection));
    await flushPromises();
    expect(wrapper.emitted("connected")).toBeUndefined();
  });

  it.each([
    ["credentials_rejected", "email or card number"],
    ["account_locked", "locked"],
    ["captcha_required", "verification"],
    ["login_timeout", "timed out"],
    ["browser_unavailable", "temporarily unavailable"],
    ["login_busy", "in progress"],
    ["connection_busy", "in progress"],
    ["login_rate_limited", "limiting attempts"],
    ["provider_unavailable", "temporarily unavailable"],
    ["rate_limited", "limiting attempts"],
    ["provider_response_invalid", "Could not connect"],
    ["login_failed", "Could not connect"],
    ["account_mismatch", "correct store account"],
    ["account_identity_unavailable", "could not be verified"],
  ])("explains %s without exposing provider diagnostics", async (code: string, message: string) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code, message: "private-provider-text" } }, { status: 422 }),
    ));
    const wrapper = render();
    await enterCredentials(wrapper);
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain(message);
    expect(wrapper.emitted("connected")).toBeUndefined();
    expect(wrapper.html()).not.toContain("private-");
    expect(wrapper.get('button[type="submit"]').attributes("disabled")).toBeUndefined();
  });

  it("localizes the credential form and cancels without signing in", async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render("cs");
    expect(wrapper.text()).toContain("E-mail nebo číslo karty");
    expect(wrapper.text()).toContain("Heslo do Globusu");
    await wrapper.get('button[type="button"]').trigger("click");
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
