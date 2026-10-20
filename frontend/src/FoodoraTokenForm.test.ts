import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import FoodoraTokenForm from "./components/FoodoraTokenForm.vue";
import { createAppI18n } from "./i18n";
import { sourceName } from "./names";

afterEach(() => vi.unstubAllGlobals());

const endpoint = "/api/v1/providers/foodora/connections/account-1/login";
const connection = { id: "account-1", provider: "foodora", status: "connected" };
const methodResult = { step: "method", flow_id: "flow-1", methods: ["EMAIL", "SMS"], method: null, connection: null };
const otpResult = { step: "otp", flow_id: "flow-1", methods: [], method: "SMS", connection: null };
const completedResult = { step: "completed", flow_id: null, methods: [], method: null, connection };

function render() {
  return mount(FoodoraTokenForm, {
    props: { connectionId: "account-1" },
    global: { plugins: [createAppI18n("en")] },
  });
}

async function fill(wrapper: ReturnType<typeof render>): Promise<void> {
  await wrapper.get('input[type="email"]').setValue("person@example.cz");
  await wrapper.get('input[type="password"]').setValue(" private-password ");
  await wrapper.get("form").trigger("submit");
}

describe("foodora sign-in", () => {
  it("posts email and password once, clears the password and emits the account", async () => {
    const connection = { id: "account-1", provider: "foodora", status: "connected" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ step: "completed", connection, methods: [] }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/providers/foodora/connections/account-1/login", expect.objectContaining({
      method: "POST", body: JSON.stringify({ email: "person@example.cz", password: " private-password " }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(wrapper.html()).not.toContain("private-password");
  });

  it("explains rejected credentials", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code: "credentials_rejected" } }, { status: 422 }),
    ));
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("email or password");
  });

  it("offers supported methods, sends the selected method and verifies the code", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(methodResult))
      .mockResolvedValueOnce(Response.json(otpResult))
      .mockResolvedValueOnce(Response.json(completedResult));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    expect(wrapper.find('input[type="password"]').exists()).toBe(false);
    expect(wrapper.findAll("option").map((option) => option.attributes("value"))).toEqual(["EMAIL", "SMS"]);
    await wrapper.get("select").setValue("SMS");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(fetcher).toHaveBeenNthCalledWith(2, `${endpoint}/flow-1/send`, expect.objectContaining({
      method: "POST", body: JSON.stringify({ method: "SMS" }),
    }));
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("123456");
    await wrapper.get("form").trigger("submit");
    expect((wrapper.get('input[autocomplete="one-time-code"]').element as HTMLInputElement).value).toBe("");
    await flushPromises();
    expect(fetcher).toHaveBeenNthCalledWith(3, `${endpoint}/flow-1/verify`, expect.objectContaining({
      method: "POST", body: JSON.stringify({ code: "123456" }),
    }));
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    wrapper.unmount();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("keeps a rejected OTP flow available for retry", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(otpResult))
      .mockResolvedValueOnce(Response.json({ error: { code: "otp_rejected" } }, { status: 422 }))
      .mockResolvedValueOnce(Response.json(completedResult));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("1234");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("code");
    expect(wrapper.find('input[type="email"]').exists()).toBe(false);
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("5678");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.emitted("connected")?.[0]).toEqual([connection]);
    expect(fetcher).toHaveBeenNthCalledWith(3, `${endpoint}/flow-1/verify`, expect.objectContaining({ method: "POST" }));
  });

  it("rejects nonnumeric or incorrectly sized codes without sending them", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(otpResult));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    for (const code of ["123", "12345678901", "abcd"]) {
      await wrapper.get('input[autocomplete="one-time-code"]').setValue(code);
      await wrapper.get("form").trigger("submit");
      await flushPromises();
      expect(wrapper.get('[role="alert"]').text()).toContain("code");
    }
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("resets an expired login so credentials can be entered again", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(otpResult))
      .mockResolvedValueOnce(Response.json({ error: { code: "login_expired" } }, { status: 422 }))
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("1234");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('input[type="email"]').exists()).toBe(true);
    expect(wrapper.get('[role="alert"]').text()).toContain("expired");
  });

  it.each(["rate_limited", "provider_unavailable", "network_error", "service_unavailable"])("resets an OTP flow discarded by the provider after %s", async (code) => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(otpResult))
      .mockResolvedValueOnce(Response.json({ error: { code } }, { status: 503 }))
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("1234");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('input[type="email"]').exists()).toBe(true);
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false);
    expect(wrapper.get('[role="alert"]').text()).toContain("temporarily unavailable");
    expect(fetcher).toHaveBeenLastCalledWith(`${endpoint}/flow-1`, expect.objectContaining({ method: "DELETE" }));
  });

  it("discards an active flow on cancel", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(methodResult))
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    await wrapper.get('button[type="button"]').trigger("click");
    expect(wrapper.emitted("cancel")).toHaveLength(1);
    expect(fetcher).toHaveBeenLastCalledWith(`${endpoint}/flow-1`, expect.objectContaining({ method: "DELETE" }));
    wrapper.unmount();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("discards flows returned after the form is closed", async () => {
    let resolve: (value: Response) => void = () => undefined;
    const fetcher = vi.fn<typeof fetch>()
      .mockReturnValueOnce(new Promise<Response>((done) => { resolve = done; }))
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const wrapper = render();
    await fill(wrapper);
    wrapper.unmount();
    resolve(Response.json(methodResult));
    await flushPromises();
    expect(fetcher).toHaveBeenLastCalledWith(`${endpoint}/flow-1`, expect.objectContaining({ method: "DELETE" }));
    expect(wrapper.emitted("connected")).toBeUndefined();
  });

  it.each([
    ["password_login_unavailable", "password sign-in"],
    ["account_not_found", "password sign-in"],
    ["challenge_required", "verification"],
    ["login_busy", "progress"],
    ["connection_busy", "progress"],
    ["login_rate_limited", "temporarily unavailable"],
    ["rate_limited", "temporarily unavailable"],
    ["provider_unavailable", "temporarily unavailable"],
    ["login_unavailable", "temporarily unavailable"],
    ["account_mismatch", "correct store account"],
    ["account_identity_unavailable", "correct store account"],
    ["unknown_failure", "Could not connect"],
  ])("explains %s without exposing provider responses", async (code, message) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      Response.json({ error: { code, message: "private-provider-payload" } }, { status: 422 }),
    ));
    const wrapper = render();
    await fill(wrapper);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain(message);
    expect(wrapper.html()).not.toContain("private-provider-payload");
  });

  it("translates foodora fee and discount slugs", () => {
    const { t } = createAppI18n("cs").global;
    expect(sourceName("delivery_fee", t)).toBe("Doprava");
    expect(sourceName("rider_tip", t)).toBe("Spropitné kurýrovi");
    expect(sourceName("voucher", t)).toBe("Voucher");
  });
});
