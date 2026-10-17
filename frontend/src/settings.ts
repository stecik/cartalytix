import { useMutation, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "./api";
import type { User } from "./api";
import { sessionOptions } from "./auth";

export type UserSettings = Partial<Pick<User, "locale" | "receipt_view">>;

export function useUserSettings() {
  const client = useQueryClient();
  const { locale } = useI18n();
  return useMutation({
    mutationFn: (patch: UserSettings) =>
      request<User>("/auth/me", { method: "PATCH", body: JSON.stringify(patch) }),
    onMutate: async (patch) => {
      await client.cancelQueries({ queryKey: sessionOptions.queryKey });
      const previous = client.getQueryData<User | null>(sessionOptions.queryKey);
      const previousLocale = locale.value;
      if (previous) client.setQueryData(sessionOptions.queryKey, { ...previous, ...patch });
      if (patch.locale) locale.value = patch.locale;
      return { previous, previousLocale };
    },
    onError: (_error, _patch, context) => {
      if (!context) return;
      client.setQueryData(sessionOptions.queryKey, context.previous);
      locale.value = context.previousLocale;
    },
    onSuccess: (user) => client.setQueryData(sessionOptions.queryKey, user),
  });
}
