import { flushPromises, mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { defineComponent, h } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { User } from "./api";
import { sessionOptions } from "./auth";
import { createAppI18n } from "./i18n";
import { useUserSettings } from "./settings";
import { setTheme, themePreference } from "./theme";

afterEach(() => {
  vi.unstubAllGlobals();
  setTheme("system");
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("queued account preferences", () => {
  it.each([
    { instances: 1, secondFails: false },
    { instances: 1, secondFails: true },
    { instances: 2, secondFails: false },
    { instances: 2, secondFails: true },
  ])("serializes theme saves across $instances hooks with secondFails=$secondFails", async ({ instances, secondFails }) => {
    const user: User = { id: "owner", email: "owner@example.com", locale: "en", receipt_view: "rendered", theme: "light", dashboard_period: "12m", dashboard_category_slices: 9, cycle_start_day: 1, cycle_weekend_shift: false, default_sync_interval_minutes: 360, is_admin: false, must_change_password: false };
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    client.setQueryData(sessionOptions.queryKey, user);
    setTheme("light");
    const pending: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>((resolve) => pending.push(resolve)));
    vi.stubGlobal("fetch", fetchMock);
    const hooks: Array<ReturnType<typeof useUserSettings>> = [];
    const probe = defineComponent({
      setup() {
        hooks.push(useUserSettings());
        return () => h("div");
      },
    });
    const wrappers = Array.from({ length: instances }, () => mount(probe, {
      global: { plugins: [createAppI18n("en"), [VueQueryPlugin, { queryClient: client }]] },
    }));
    try {
      hooks[0]?.mutate({ theme: "dark" });
      await flushPromises();
      hooks[instances - 1]?.mutate({ theme: "system" });
      await flushPromises();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(themePreference.value).toBe("dark");
      pending[0]?.(Response.json({ detail: "Unavailable" }, { status: 503 }));
      await flushPromises();
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(themePreference.value).toBe("system");
      pending[1]?.(secondFails
        ? Response.json({ detail: "Unavailable" }, { status: 503 })
        : Response.json({ ...user, theme: "system" }));
      await flushPromises();
      expect(themePreference.value).toBe(secondFails ? "light" : "system");
      expect(client.getQueryData<User>(sessionOptions.queryKey)?.theme).toBe(secondFails ? "light" : "system");
      expect(localStorage.getItem("cartalytix-theme")).toBe(secondFails ? "light" : "system");
    } finally { for (const wrapper of wrappers) wrapper.unmount(); client.clear(); }
  });
});
