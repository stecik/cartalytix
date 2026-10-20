<script setup lang="ts">
import { computed } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { ApiError, request } from "../api";
import type { McpAuthorization } from "../api";
import QueryState from "../components/QueryState.vue";

const { t } = useI18n();
const route = useRoute();
const requestId = computed(() => (typeof route.query.request === "string" ? route.query.request : ""));
const path = computed(() => `/mcp/authorizations/${encodeURIComponent(requestId.value)}`);
const authorization = useQuery({
  queryKey: ["mcp-authorization", requestId],
  queryFn: ({ signal }) => request<McpAuthorization>(path.value, { signal }),
  enabled: computed(() => requestId.value !== ""),
  retry: false,
  staleTime: Infinity,
});
const expired = computed(
  () => requestId.value === "" || (authorization.error.value instanceof ApiError && authorization.error.value.status === 404),
);
const decide = useMutation({
  mutationFn: (approve: boolean) =>
    request<{ redirect_url: string }>(path.value, { method: "POST", body: JSON.stringify({ approve }) }),
  onSuccess: ({ redirect_url }) => window.location.assign(redirect_url),
});
const scopeLabels: Record<string, string> = {
  "cartalytix:read": "mcpScopeRead",
  "cartalytix:categories:write": "mcpScopeCategoriesWrite",
  offline_access: "mcpScopeOffline",
};
</script>

<template>
  <div class="view-content consent-workspace">
    <section class="consent-panel" aria-labelledby="consent-heading">
      <p v-if="expired" role="alert" class="form-alert">{{ t("mcpRequestExpired") }}</p>
      <QueryState v-else :pending="authorization.isPending.value" :failed="authorization.isError.value" @retry="authorization.refetch()">
        <template v-if="authorization.data.value">
          <h1 id="consent-heading">{{ t("mcpConsentTitle", { client: authorization.data.value.client_name }) }}</h1>
          <p>{{ t("mcpConsentIntro", { client: authorization.data.value.client_name }) }}</p>
          <ul class="consent-scopes">
            <li v-for="scope in authorization.data.value.scopes" :key="scope">{{ t(scopeLabels[scope] ?? "mcpScopeRead") }}</li>
          </ul>
          <p class="consent-return">{{ t("mcpConsentReturn", { host: authorization.data.value.redirect_host }) }}</p>
          <p v-if="decide.isError.value" role="alert" class="form-alert">{{ t("mcpConsentFailed") }}</p>
          <div class="consent-actions">
            <button type="button" class="button secondary" :disabled="decide.isPending.value || decide.isSuccess.value" @click="decide.mutate(false)">{{ t("mcpDeny") }}</button>
            <button type="button" class="button primary" :disabled="decide.isPending.value || decide.isSuccess.value" @click="decide.mutate(true)">{{ t("mcpAllow") }}</button>
          </div>
        </template>
      </QueryState>
    </section>
  </div>
</template>

<style scoped>
.consent-workspace { max-width: 620px; }
.consent-panel { padding: 28px; border: 1px solid var(--border-store); border-radius: 12px; background: var(--surface-store); }
.consent-panel h1 { margin-bottom: 12px; font-size: 24px; line-height: 1.3; font-weight: 600; letter-spacing: -.6px; overflow-wrap: anywhere; }
.consent-panel p { color: var(--muted); font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.consent-scopes { margin: 18px 0; padding-left: 20px; font-size: 13px; line-height: 1.9; }
.consent-return { margin-bottom: 8px; }
.consent-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 24px; }
@media (max-width: 600px) { .consent-panel { padding: 20px; } }
</style>
