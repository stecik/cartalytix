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
import { isThemePreference, themePreference } from "../theme";
import AppDialog from "../components/AppDialog.vue";
import AppIcon from "../components/AppIcon.vue";
import AppSelect from "../components/AppSelect.vue";
import DiagnosticLogsDialog from "../components/DiagnosticLogsDialog.vue";
import CredentialsDialog from "../components/CredentialsDialog.vue";
import McpScopeSwitches from "../components/McpScopeSwitches.vue";
import SegmentedToggle from "../components/SegmentedToggle.vue";

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
const SYNC_INTERVALS = [60, 360, 1440] as const;
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
const syncOptions = computed(() => {
  const current = session.data.value?.default_sync_interval_minutes;
  const custom = current && !SYNC_INTERVALS.some((option) => option === current)
    ? [{ value: String(current), label: t("everyMinutes", { count: current }) }]
    : [];
  return [
    { value: "60", label: t("everyHour") },
    { value: "360", label: t("everySixHours") },
    { value: "1440", label: t("everyDay") },
    ...custom,
    { value: "manual", label: t("manualOnly") },
  ];
});
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
    <header class="settings-heading">
      <h1>{{ t("settings") }}</h1>
      <p>{{ t("settingsIntro") }}</p>
    </header>
    <p v-if="settings.isError.value" role="alert" class="form-alert settings-error">{{ t("settingsSaveFailed") }}</p>
    <section class="settings-section" aria-labelledby="account-preferences-heading">
      <header class="settings-section-heading">
        <h2 id="account-preferences-heading">{{ t("accountPreferences") }}</h2>
        <span class="settings-save-note" :class="{ saved: settings.isSuccess.value }" aria-live="polite">{{ t(settings.isPending.value ? "saving" : settings.isSuccess.value ? "settingsSaved" : "settingsAutoSave") }}</span>
      </header>
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
          <SegmentedToggle v-model="receiptView" class="settings-receipt-toggle" :options="views" :label="t('receiptDisplay')" aria-describedby="receipt-display-hint" />
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
          <button type="button" class="button secondary settings-install" data-action="apply-default-sync" :disabled="settings.isPending.value" @click="applySync.reset(); applyingSync = true">{{ t("applyDefaultSync") }}</button>
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
    </section>
    <section class="settings-section" aria-labelledby="application-settings-heading">
      <header class="settings-section-heading">
        <h2 id="application-settings-heading">{{ t("application") }}</h2>
        <span class="settings-save-note">{{ t("thisDevice") }}</span>
      </header>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("homeScreenAccess") }}</h3>
          <p>{{ t("installDescription") }}</p>
        </div>
        <button type="button" class="button secondary settings-install" @click="installApp">
          <AppIcon name="download" />{{ t("install") }}
        </button>
      </div>
      <div v-if="showInstallHelp" class="settings-install-help">
        <p role="status">{{ t("installHelp") }}</p>
      </div>
      <div v-if="session.data.value && !isAdmin" class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("diagnosticsDownload") }}</h3>
          <p>{{ t("diagnosticsHint") }}</p>
        </div>
        <button type="button" class="button secondary settings-install" data-action="download-logs" @click="downloadingLogs = true"><AppIcon name="download" />{{ t("diagnosticsDownload") }}</button>
      </div>
    </section>
    <section v-if="session.data.value" class="settings-section" aria-labelledby="account-security-heading">
      <header class="settings-section-heading">
        <h2 id="account-security-heading">{{ t("accountSecurity") }}</h2>
        <span class="settings-save-note" :class="{ saved: credentialsSaved }" aria-live="polite">{{ credentialsSaved ? t("signInDetailsSaved") : "" }}</span>
      </header>
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("signInDetails") }}</h3>
          <p>{{ t("signInDetailsHint", { email: session.data.value.email }) }}</p>
        </div>
        <button type="button" class="button secondary settings-install" data-action="change-credentials" @click="credentialsSaved = false; editingCredentials = true">{{ t("changeSignInDetails") }}</button>
      </div>
    </section>
    <section v-if="!isAdmin && Array.isArray(grants.data.value?.items)" class="settings-section" aria-labelledby="mcp-heading">
      <header class="settings-section-heading">
        <h2 id="mcp-heading">{{ t("mcpApps") }}</h2>
        <span class="settings-save-note">{{ t("mcpReadOnly") }}</span>
      </header>
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
        <button type="button" class="button secondary settings-install" @click="revoking = grant">{{ t("mcpRevoke") }}</button>
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
    </section>
    <DiagnosticLogsDialog v-if="downloadingLogs && session.data.value && !isAdmin" @close="downloadingLogs = false" />
    <CredentialsDialog v-if="editingCredentials && session.data.value" :email="session.data.value.email" @close="editingCredentials = false" @saved="onCredentialsSaved" />
    <AppDialog v-if="applyingSync" :title="t('applyDefaultSync')" :busy="applySync.isPending.value" @close="applyingSync = false">
      <p>{{ t("applyDefaultSyncHelp") }}</p>
      <p v-if="applySync.isError.value" class="form-alert" role="alert">{{ t("applyDefaultSyncFailed") }}</p>
      <div class="dialog-actions">
        <button class="button secondary" data-action="cancel" type="button" :disabled="applySync.isPending.value" @click="applyingSync = false">{{ t("cancel") }}</button>
        <button class="button primary" data-action="confirm-apply-default-sync" type="button" :disabled="applySync.isPending.value" @click="applySync.mutate()">{{ t("applyDefaultSync") }}</button>
      </div>
    </AppDialog>
    <AppDialog v-if="revoking" :title="t('mcpRevokeTitle')" :busy="revoke.isPending.value" @close="revoking = null">
      <p>{{ t("mcpRevokeHelp", { client: revoking.client_name }) }}</p>
      <p v-if="revoke.isError.value" class="form-alert" role="alert">{{ t("mcpRevokeFailed") }}</p>
      <div class="dialog-actions">
        <button class="button secondary" data-action="cancel" type="button" :disabled="revoke.isPending.value" @click="revoking = null">{{ t("cancel") }}</button>
        <button class="button danger" type="button" :disabled="revoke.isPending.value" @click="revoking && revoke.mutate(revoking.id)">{{ t("mcpRevoke") }}</button>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.settings-workspace { max-width: 1040px; }
.settings-heading { margin-bottom: 34px; }
.settings-heading h1 { font-size: 29px; line-height: 1.25; font-weight: 600; letter-spacing: -1px; margin-bottom: 9px; }
.settings-heading p { color: var(--muted); font-size: 13px; line-height: 1.7; }
.settings-error { margin-bottom: 20px; }
.settings-section { border: 1px solid var(--border-store); border-radius: 12px; background: var(--surface-store); }
.settings-section + .settings-section { margin-top: 28px; }
.settings-section-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px 20px; padding: 18px 24px; border-bottom: 1px solid var(--border-store-header); border-radius: 11px 11px 0 0; background: var(--surface-store-header); }
.settings-section-heading h2 { font-size: 16px; font-weight: 650; letter-spacing: -.3px; }
.settings-save-note { color: var(--muted); font-size: 11px; line-height: 1.6; }
.settings-save-note.saved { color: var(--status-connected); }
.settings-row { display: grid; grid-template-columns: minmax(0, 1fr) 320px; align-items: center; gap: 28px; margin-inline: 24px; padding-block: 24px; }
.settings-row + .settings-row { border-top: 1px solid var(--line); }
.settings-copy { min-width: 0; }
.settings-copy h3 { margin-bottom: 6px; font-size: 14px; font-weight: 600; }
.settings-copy p { max-width: 52ch; color: var(--muted); font-size: 12px; line-height: 1.7; }
.settings-control { min-width: 0; }
.settings-control .settings-receipt-toggle { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); width: 100%; margin: 0; padding: 3px; border-radius: 8px; }
.settings-control .settings-receipt-toggle :deep(label) { display: flex; align-items: center; justify-content: center; min-height: 36px; padding: 8px 10px; border-radius: 5px; white-space: normal; text-align: center; }
.settings-select { display: block; width: 100%; }
.settings-sync-control { display: grid; gap: 10px; }
.settings-sync-control .settings-install { justify-self: stretch; }
.settings-copy p.settings-sync-applied { margin-top: 6px; color: var(--status-connected); }
.settings-select :deep(.app-select-trigger) { min-height: 44px; border-radius: 8px; }
.settings-install { justify-self: end; min-height: 44px; padding: 10px 16px; border-radius: 8px; font-size: 13px; }
.settings-install svg { width: 18px; height: 18px; }
.settings-install-help { margin: 0 24px 24px; padding: 14px 16px; border-left: 2px solid var(--border-account-panel); border-radius: 0 6px 6px 0; background: var(--surface-account-panel); }
.settings-install-help p { color: var(--muted); font-size: 12px; line-height: 1.8; }
.settings-connector-url { min-width: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.settings-row > p.settings-copy { color: var(--muted); font-size: 12px; }
.settings-grant-scopes,
.settings-grant-error { grid-column: 1 / -1; }
.settings-grant-scopes { margin-top: -8px; }
@media (max-width: 780px) { .settings-row { grid-template-columns: minmax(0, 1fr); align-items: start; gap: 16px; }.settings-install { justify-self: start; } }
@media (max-width: 600px) { .settings-section-heading { padding: 16px; }.settings-row { margin-inline: 16px; padding-block: 20px; }.settings-install-help { margin: 0 16px 20px; }.settings-section + .settings-section { margin-top: 22px; } }
</style>
