import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { describe, expect, it, vi } from "vitest";
import type { AdminUser } from "./api";
import { createAppI18n } from "./i18n";
import AccountsView from "./views/AccountsView.vue";

const users: AdminUser[] = [
  { id: "a", email: "boss@example.com", is_admin: true, is_disabled: false, must_change_password: false, created_at: "2026-10-01T10:00:00Z" },
  { id: "u", email: "user@example.com", is_admin: false, is_disabled: false, must_change_password: false, created_at: "2026-10-02T10:00:00Z" },
];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

function render(fetch: typeof globalThis.fetch) {
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return mount(AccountsView, {
    attachTo: document.body,
    global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
  });
}

describe("account management", () => {
  it("lists accounts and protects administrators from management actions", async () => {
    const wrapper = render(vi.fn<typeof globalThis.fetch>().mockResolvedValue(json({ items: users })));
    await flushPromises();
    const rows = wrapper.findAll("tbody tr");
    expect(rows.map((row) => row.find("td").text())).toEqual(["boss@example.com", "user@example.com"]);
    expect(rows[0]?.findAll("button")).toHaveLength(0);
    expect(rows[1]?.text()).toContain("Reset password");
    wrapper.unmount();
  });

  it("creates an account and shows its generated password once", async () => {
    const created = { user: { ...users[1], id: "n", email: "new@example.com", must_change_password: true }, password: "generated-secret-1234" };
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(async (_url, init) =>
      init?.method === "POST" ? json(created, 201) : json({ items: users }));
    const wrapper = render(fetch);
    await flushPromises();
    await wrapper.get("#account-email").setValue(" new@example.com ");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const post = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ email: "new@example.com" });
    expect(document.body.querySelector(".generated-password")?.textContent).toBe("generated-secret-1234");
    expect((wrapper.get("#account-email").element as HTMLInputElement).value).toBe("");
    wrapper.unmount();
  });

  it("deletes an account only after confirmation", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(async (_url, init) =>
      init?.method === "DELETE" ? new Response(null, { status: 204 }) : json({ items: users }));
    const wrapper = render(fetch);
    await flushPromises();
    const remove = wrapper.findAll("tbody tr")[1]?.findAll("button").find((button) => button.text() === "Delete");
    await remove?.trigger("click");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    const confirm = [...document.body.querySelectorAll<HTMLButtonElement>(".dialog-actions button.danger")][0];
    confirm?.click();
    await flushPromises();
    const call = fetch.mock.calls.find(([, init]) => init?.method === "DELETE");
    expect(call?.[0]).toBe("/api/v1/admin/users/u");
    wrapper.unmount();
  });
});
