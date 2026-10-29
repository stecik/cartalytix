<script setup lang="ts">
import { computed, inject, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { McpGrant, McpScope, User } from "../api";
import { sessionOptions } from "../auth";
import { useUserSettings } from "../settings";
import { pwaInstallKey } from "../pwaInstall";
import { PRESET_PERIODS } from "../stats";
import { SYNC_INTERVALS, syncIntervalOptions } from "../syncIntervals";
import { isThemePreference, themePreference } from "../theme";
import AppDialog from "../components/AppDialog.vue";
import AppIcon from "../components/AppIcon.vue";
import AppSelect from "../components/AppSelect.vue";
import DiagnosticLogsDialog from "../components/DiagnosticLogsDialog.vue";
import CredentialsDialog from "../components/CredentialsDialog.vue";
import McpScopeSwitches from "../components/McpScopeSwitches.vue";
import PageHeader from "../components/PageHeader.vue";
import AppCard from "../components/AppCard.vue";
import SegmentedToggle from "../components/SegmentedToggle.vue";
import AppButton from "../components/AppButton.vue";

const { t, locale } = useI18n();
const queryClient = useQueryClient();
const session = useQuery(sessionOptions);
// Administrators only manage accounts, so purchase-related preferences are hidden.
const isAdmin = computed(() => session.data.value?.is_admin === true);
// The connector is disabled (404) unless the instance has a public URL configured.
const grants = useQuery({
  queryKey: ["mcp-grants"],
  queryFn: ({ signal }) => request<{ items: McpGrant[] }>("/mcp/grants", { signal }),
  retry: false,
  enabled: computed(() => !isAdmin.value),
});
const connectorUrl = `${window.location.origin}/mcp`;
const revoking = ref<McpGrant | null>(null);
const revoke = useMutation({
  mutationFn: (id: string) => request<void>(`/mcp/grants/${encodeURIComponent(id)}`, { method: "DELETE" }),
  onSuccess: async () => {
    revoking.value = null;
    await queryClient.invalidateQueries({ queryKey: ["mcp-grants"] });
  },
});
const permissions = useMutation({
  mutationFn: ({ id, scopes }: { id: string; scopes: McpScope[] }) =>
    request<McpGrant>(`/mcp/grants/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ scopes }) }),
  onSuccess: (saved) => {
    queryClient.setQueryData<{ items: McpGrant[] }>(["mcp-grants"], (current) =>
      current && { items: current.items.map((grant) => (grant.id === saved.id ? saved : grant)) });
  },
});
function connectedOn(value: string): string {
  return new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", { dateStyle: "medium" }).format(new Date(value));
}
const settings = useUserSettings();
const editingCredentials = ref(false);
const credentialsSaved = ref(false);
function onCredentialsSaved(): void {
  editingCredentials.value = false;
  credentialsSaved.value = true;
}
const pwaInstall = inject(pwaInstallKey);
const showInstallHelp = ref(false);
const downloadingLogs = ref(false);

async function installApp(): Promise<void> {
  if (pwaInstall?.available.value) {
    showInstallHelp.value = false;
    await pwaInstall.install();
  } else {
    showInstallHelp.value = true;
  }
}
const receiptView = computed({
  get: (): User["receipt_view"] => session.data.value?.receipt_view ?? "rendered",
  set: (value: User["receipt_view"]) => settings.mutate({ receipt_view: value }),
});
const language = computed({
  get: () => locale.value,
  set: (value: string) => settings.mutate({ locale: value }),
});
const languages = computed(() => [
  { value: "en", label: t("english") },
  { value: "cs", label: t("czech") },
]);
const theme = computed({
  get: () => session.data.value?.theme ?? themePreference.value,
  set: (value: string) => { if (isThemePreference(value)) settings.mutate({ theme: value }); },
});
const themes = computed(() => [
  { value: "system", label: t("themeSystem") },
  { value: "light", label: t("themeLight") },
  { value: "dark", label: t("themeDark") },
]);
const dashboardPeriod = computed({
  get: (): string => session.data.value?.dashboard_period ?? "12m",
  set: (value: string) => {
    const period = PRESET_PERIODS.find((preset) => preset === value);
    if (period) settings.mutate({ dashboard_period: period });
  },
});
// Eight distinguishable category colors plus one segment for the rest.
const CATEGORY_SLICES = [3, 4, 5, 6, 7, 8, 9] as const;
const categorySlices = computed({
  get: (): string => String(session.data.value?.dashboard_category_slices ?? 9),
  set: (value: string) => {
    const slices = CATEGORY_SLICES.find((option) => String(option) === value);
    if (slices) settings.mutate({ dashboard_category_slices: slices });
  },
});
// "manual" stands for null, which AppSelect cannot carry as a value.
const defaultSync = computed({
  get: (): string => {
    const value = session.data.value?.default_sync_interval_minutes;
    return value === null ? "manual" : String(value ?? 360);
  },
  set: (value: string) => {
    if (value === "manual") settings.mutate({ default_sync_interval_minutes: null });
    const interval = SYNC_INTERVALS.find((option) => String(option) === value);
    if (interval) settings.mutate({ default_sync_interval_minutes: interval });
  },
});
const syncOptions = computed(() => [
  ...syncIntervalOptions(t, session.data.value?.default_sync_interval_minutes),
  { value: "manual", label: t("manualOnly") },
]);
const applyingSync = ref(false);
const applySync = useMutation({
  mutationFn: () => request<void>("/connections/apply-default-sync", { method: "POST" }),
  onSuccess: async () => {
    applyingSync.value = false;
    await queryClient.invalidateQueries({ queryKey: ["connections"] });
  },
});
const categorySliceOptions = CATEGORY_SLICES.map((value) => ({ value: String(value), label: String(value) }));
const dashboardPeriods = computed(() =>
  PRESET_PERIODS.map((value) => ({ value, label: t(`period_${value}`) })),
);
const views = computed(() => [
  { value: "rendered" as const, label: t("receiptViewRendered") },
  { value: "detailed" as const, label: t("receiptViewDetailed") },
]);
</script>

<template>
  <div class="view-content settings-workspace">
    <PageHeader :title="t('settings')" :intro="t('settingsIntro')" />
    <p v-if="settings.isError.value" role="alert" class="form-alert settings-error">{{ t("settingsSaveFailed") }}</p>
    <AppCard class="settings-section" aria-labelledby="account-preferences-heading">
      <template #header>
        <h2 id="account-preferences-heading">{{ t("accountPreferences") }}</h2>
        <span class="settings-save-note" :class="{ saved: settings.isSuccess.value }" aria-live="polite">{{ t(settings.isPending.value ? "saving" : settings.isSuccess.value ? "settingsSaved" : "settingsAutoSave") }}</span>
      </template>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("appearance") }}</h3>
          <p>{{ t("themeHint") }}</p>
        </div>
        <div class="settings-control">
          <AppSelect v-model="theme" class="settings-select" :options="themes" :label="t('appearance')" />
        </div>
      </div>
      <div v-if="!isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("receiptDisplay") }}</h3>
          <p id="receipt-display-hint">{{ t(receiptView === "rendered" ? "receiptViewRenderedHint" : "receiptViewDetailedHint") }}</p>
        </div>
        <div class="settings-control">
          <SegmentedToggle v-model="receiptView" block :options="views" :label="t('receiptDisplay')" aria-describedby="receipt-display-hint" />
        </div>
      </div>
      <div v-if="!isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("dashboardPeriod") }}</h3>
          <p>{{ t("dashboardPeriodHint") }}</p>
        </div>
        <div class="settings-control">
          <AppSelect v-model="dashboardPeriod" class="settings-select" :options="dashboardPeriods" :label="t('dashboardPeriod')" />
        </div>
      </div>
      <div v-if="!isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("dashboardCategorySlices") }}</h3>
          <p>{{ t("dashboardCategorySlicesHint") }}</p>
        </div>
        <div class="settings-control">
          <AppSelect v-model="categorySlices" class="settings-select" :options="categorySliceOptions" :label="t('dashboardCategorySlices')" />
        </div>
      </div>
      <div v-if="session.data.value && !isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("defaultSync") }}</h3>
          <p>{{ t("defaultSyncHint") }}</p>
          <p v-if="applySync.isSuccess.value" class="settings-sync-applied" role="status">{{ t("applyDefaultSyncDone") }}</p>
        </div>
        <div class="settings-control settings-sync-control">
          <AppSelect v-model="defaultSync" class="settings-select" :options="syncOptions" :label="t('defaultSync')" />
          <AppButton type="button" class="settings-install" data-action="apply-default-sync" :disabled="settings.isPending.value" @click="applySync.reset(); applyingSync = true">{{ t("applyDefaultSync") }}</AppButton>
        </div>
      </div>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("language") }}</h3>
          <p>{{ t("settingsLanguageHint") }}</p>
        </div>
        <div class="settings-control">
          <AppSelect v-model="language" class="settings-select" :options="languages" :label="t('language')" />
        </div>
      </div>
    </AppCard>
    <AppCard class="settings-section" aria-labelledby="application-settings-heading">
      <template #header>
        <h2 id="application-settings-heading">{{ t("application") }}</h2>
        <span class="settings-save-note">{{ t("thisDevice") }}</span>
      </template>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("homeScreenAccess") }}</h3>
          <p>{{ t("installDescription") }}</p>
        </div>
        <AppButton type="button" class="settings-install" @click="installApp">
          <AppIcon name="download" />{{ t("install") }}
        </AppButton>
      </div>
      <div v-if="showInstallHelp" class="settings-install-help">
        <p role="status">{{ t("installHelp") }}</p>
      </div>
      <div v-if="session.data.value && !isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("diagnosticsDownload") }}</h3>
          <p>{{ t("diagnosticsHint") }}</p>
        </div>
        <AppButton type="button" class="settings-install" data-action="download-logs" @click="downloadingLogs = true"><AppIcon name="download" />{{ t("diagnosticsDownload") }}</AppButton>
      </div>
    </AppCard>
    <AppCard v-if="session.data.value" class="settings-section" aria-labelledby="account-security-heading">
      <template #header>
        <h2 id="account-security-heading">{{ t("accountSecurity") }}</h2>
        <span class="settings-save-note" :class="{ saved: credentialsSaved }" aria-live="polite">{{ credentialsSaved ? t("signInDetailsSaved") : "" }}</span>
      </template>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("signInDetails") }}</h3>
          <p>{{ t("signInDetailsHint", { email: session.data.value.email }) }}</p>
        </div>
        <AppButton type="button" class="settings-install" data-action="change-credentials" @click="credentialsSaved = false; editingCredentials = true">{{ t("changeSignInDetails") }}</AppButton>
      </div>
    </AppCard>
    <AppCard v-if="!isAdmin && Array.isArray(grants.data.value?.items)" class="settings-section" aria-labelledby="mcp-heading">
      <template #header>
        <h2 id="mcp-heading">{{ t("mcpApps") }}</h2>
        <span class="settings-save-note">{{ t("mcpReadOnly") }}</span>
      </template>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("mcpConnectorUrl") }}</h3>
          <p>{{ t("mcpConnectorHint") }}</p>
        </div>
        <code class="settings-connector-url">{{ connectorUrl }}</code>
      </div>
      <div v-for="grant in grants.data.value?.items" :key="grant.id" class="settings-row">
        <div class="settings-copy">
          <h3>{{ grant.client_name }}</h3>
          <p>
            {{ t("mcpConnectedOn", { date: connectedOn(grant.created_at) }) }}
            <template v-if="grant.last_used_at"> · {{ t("mcpLastUsed", { date: connectedOn(grant.last_used_at) }) }}</template>
          </p>
        </div>
        <AppButton type="button" class="settings-install" @click="revoking = grant">{{ t("mcpRevoke") }}</AppButton>
        <McpScopeSwitches
          class="settings-grant-scopes"
          :model-value="grant.scopes"
          :disabled="permissions.isPending.value && permissions.variables.value?.id === grant.id"
          @update:model-value="permissions.mutate({ id: grant.id, scopes: $event })"
          @revoke="revoking = grant"
        />
        <p v-if="permissions.isError.value && permissions.variables.value?.id === grant.id" class="form-alert settings-grant-error" role="alert">{{ t("mcpPermissionsFailed") }}</p>
      </div>
      <div v-if="grants.data.value?.items.length === 0" class="settings-row">
        <p class="settings-copy">{{ t("mcpNoApps") }}</p>
      </div>
    </AppCard>
    <DiagnosticLogsDialog v-if="downloadingLogs && session.data.value && !isAdmin" @close="downloadingLogs = false" />
    <CredentialsDialog v-if="editingCredentials && session.data.value" :email="session.data.value.email" @close="editingCredentials = false" @saved="onCredentialsSaved" />
    <AppDialog v-if="applyingSync" :title="t('applyDefaultSync')" :busy="applySync.isPending.value" @close="applyingSync = false">
      <p>{{ t("applyDefaultSyncHelp") }}</p>
      <p v-if="applySync.isError.value" class="form-alert" role="alert">{{ t("applyDefaultSyncFailed") }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="applySync.isPending.value" @click="applyingSync = false">{{ t("cancel") }}</AppButton>
        <AppButton variant="primary" data-action="confirm-apply-default-sync" type="button" :disabled="applySync.isPending.value" @click="applySync.mutate()">{{ t("applyDefaultSync") }}</AppButton>
      </div>
    </AppDialog>
    <AppDialog v-if="revoking" :title="t('mcpRevokeTitle')" :busy="revoke.isPending.value" @close="revoking = null">
      <p>{{ t("mcpRevokeHelp", { client: revoking.client_name }) }}</p>
      <p v-if="revoke.isError.value" class="form-alert" role="alert">{{ t("mcpRevokeFailed") }}</p>
      <div class="dialog-actions">
        <AppButton data-action="cancel" type="button" :disabled="revoke.isPending.value" @click="revoking = null">{{ t("cancel") }}</AppButton>
        <AppButton variant="danger" type="button" :disabled="revoke.isPending.value" @click="revoking && revoke.mutate(revoking.id)">{{ t("mcpRevoke") }}</AppButton>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.settings-workspace { max-width: 1040px; }
.settings-error { margin-bottom: 20px; }
.settings-save-note { color: var(--text-muted); font-size: 11px; line-height: 1.6; }
.settings-save-note.saved { color: var(--success); }
.settings-row { display: grid; grid-template-columns: minmax(0, 1fr) 320px; align-items: center; gap: 28px; margin-inline: 24px; padding-block: 24px; }
.settings-row + .settings-row { border-top: 1px solid var(--border); }
.settings-copy { min-width: 0; }
.settings-copy h3 { margin-bottom: 6px; font-size: 14px; font-weight: 600; }
.settings-copy p { max-width: 52ch; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.settings-control { min-width: 0; }
.settings-select { display: block; width: 100%; }
.settings-sync-control { display: grid; gap: 10px; }
.settings-sync-control .settings-install { justify-self: stretch; }
.settings-copy p.settings-sync-applied { margin-top: 6px; color: var(--success); }
.settings-install { justify-self: end; }
.settings-install svg { width: 18px; height: 18px; }
.settings-install-help { margin: 0 24px 24px; padding: 14px 16px; border-left: 2px solid var(--control); border-radius: 0 6px 6px 0; background: var(--bg-inset); }
.settings-install-help p { color: var(--text-muted); font-size: 12px; line-height: 1.8; }
.settings-connector-url { min-width: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.settings-row > p.settings-copy { color: var(--text-muted); font-size: 12px; }
.settings-grant-scopes,
.settings-grant-error { grid-column: 1 / -1; }
.settings-grant-scopes { margin-top: -8px; }
@media (max-width: 780px) { .settings-row { grid-template-columns: minmax(0, 1fr); align-items: start; gap: 16px; }.settings-install { justify-self: start; } }
@media (max-width: 600px) { .settings-row { margin-inline: 16px; padding-block: 20px; }.settings-install-help { margin: 0 16px 20px; } }
</style>
