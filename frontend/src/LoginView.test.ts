import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";
import { describe, expect, it, vi } from "vitest";
import LoginView from "./views/LoginView.vue";
import { createAppI18n } from "./i18n";

describe("sign-in form", () => {
  it("keeps failed credentials private and presents a translated retryable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: "invalid_credentials" }), {
            status: 401,
          }),
        ),
    );
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/login", component: LoginView }],
    });
    await router.push("/login");
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = mount(LoginView, {
      global: {
        plugins: [
          router,
          createAppI18n("cs"),
          [VueQueryPlugin, { queryClient: client }],
        ],
      },
    });
    await wrapper.get('input[type="email"]').setValue("owner@example.com");
    await wrapper.get('input[type="password"]').setValue("incorrect");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe(
      "Nesprávný e-mail nebo heslo.",
    );
    expect(client.getQueryData(["session"])).toBeUndefined();
    expect(
      wrapper.get('button[type="submit"]').attributes("disabled"),
    ).toBeUndefined();
  });
});
