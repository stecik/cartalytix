import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it, vi } from "vitest";
import type { User } from "./api";
import { sessionOptions } from "./auth";
import { createAppI18n } from "./i18n";
import ChangeCredentialsView from "./views/ChangeCredentialsView.vue";

const admin: User = { id: "a", email: "admin@example.com", locale: "en", receipt_view: "rendered", theme: "system", dashboard_period: "12m", dashboard_category_slices: 9, default_sync_interval_minutes: 360, is_admin: true, must_change_password: true };

async function render(user: User, fetch: typeof globalThis.fetch) {
  vi.stubGlobal("fetch", fetch);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/change-credentials", component: ChangeCredentialsView },
      { path: "/", component: { template: "<p>home</p>" } },
    ],
  });
  await router.push("/change-credentials");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(sessionOptions.queryKey, user);
  const wrapper = mount(ChangeCredentialsView, {
    global: { plugins: [router, createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
  });
  return { wrapper, router, client };
}

describe("forced credentials change", () => {
  it("requires the bootstrap administrator to set a new email and password", async () => {
    const updated = { ...admin, email: "boss@example.com", must_change_password: false };
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(new Response(JSON.stringify(updated)));
    const { wrapper, router, client } = await render(admin, fetch);
    await wrapper.get("#new-email").setValue("boss@example.com");
    expect(wrapper.find("#current-password").exists()).toBe(false);
    await wrapper.get("#new-password").setValue("weakpass");
    await wrapper.get("#confirm-password").setValue("weakpass");
    await wrapper.get("form").trigger("submit");
    expect(wrapper.get('[role="alert"]').text()).toContain("uppercase letter");
    await wrapper.get("#new-password").setValue("New-pass1");
    await wrapper.get("#confirm-password").setValue("Other-pass1");
    await wrapper.get("form").trigger("submit");
    expect(wrapper.get('[role="alert"]').text()).toBe("The new passwords do not match.");
    expect(fetch).not.toHaveBeenCalled();
    await wrapper.get("#confirm-password").setValue("New-pass1");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    const [, init] = fetch.mock.calls[0] ?? [];
    expect(JSON.parse(String(init?.body))).toEqual({ new_password: "New-pass1", email: "boss@example.com" });
    expect(client.getQueryData(sessionOptions.queryKey)).toEqual(updated);
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("asks a regular user only for a password and translates API errors", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "password_unchanged" } }), { status: 422 }),
    );
    const { wrapper } = await render({ ...admin, is_admin: false }, fetch);
    expect(wrapper.find("#new-email").exists()).toBe(false);
    await wrapper.get("#new-password").setValue("Issued-pass1");
    await wrapper.get("#confirm-password").setValue("Issued-pass1");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe("Choose a password different from the current one.");
  });
});
