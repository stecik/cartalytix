import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "./api";
import type { User } from "./api";
import { sessionOptions } from "./auth";
import { setTheme, themePreference } from "./theme";

export type UserSettings = Partial<Pick<User, "locale" | "receipt_view" | "theme" | "dashboard_period">>;

export function useUserSettings() {
  const client = useQueryClient();
  const { locale } = useI18n();
  return useMutation({
    scope: { id: "user-settings" },
    mutationFn: async (patch: UserSettings) => {
      await client.cancelQueries({ queryKey: sessionOptions.queryKey });
      const previous = client.getQueryData<User | null>(sessionOptions.queryKey);
      const previousLocale = locale.value;
      const previousTheme = themePreference.value;
      if (previous) client.setQueryData(sessionOptions.queryKey, { ...previous, ...patch });
      if (patch.locale) locale.value = patch.locale;
      if (patch.theme) setTheme(patch.theme);
      try {
        return await request<User>("/auth/me", { method: "PATCH", body: JSON.stringify(patch) });
      } catch (error: unknown) {
        client.setQueryData(sessionOptions.queryKey, previous);
        locale.value = previousLocale;
        if (patch.theme) setTheme(previousTheme);
        throw error;
      }
    },
    onSuccess: (user) => client.setQueryData(sessionOptions.queryKey, user),
  });
}
