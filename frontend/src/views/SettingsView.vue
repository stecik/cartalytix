<script setup lang="ts">
import { computed, inject, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { McpGrant, User } from "../api";
import { sessionOptions } from "../auth";
import { useUserSettings } from "../settings";
import { pwaInstallKey } from "../pwaInstall";
import { isThemePreference, themePreference } from "../theme";
import AppDialog from "../components/AppDialog.vue";
import AppIcon from "../components/AppIcon.vue";
import AppSelect from "../components/AppSelect.vue";
import SegmentedToggle from "../components/SegmentedToggle.vue";

const { t, locale } = useI18n();
const queryClient = useQueryClient();
// The connector is disabled (404) unless the instance has a public URL configured.
const grants = useQuery({
  queryKey: ["mcp-grants"],
  queryFn: ({ signal }) => request<{ items: McpGrant[] }>("/mcp/grants", { signal }),
  retry: false,
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
function connectedOn(value: string): string {
  return new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", { dateStyle: "medium" }).format(new Date(value));
}
const session = useQuery(sessionOptions);
const settings = useUserSettings();
const pwaInstall = inject(pwaInstallKey);
const showInstallHelp = ref(false);

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
      <div class="settings-row">
        <div class="settings-copy">
          <h3>{{ t("receiptDisplay") }}</h3>
          <p id="receipt-display-hint">{{ t(receiptView === "rendered" ? "receiptViewRenderedHint" : "receiptViewDetailedHint") }}</p>
        </div>
        <div class="settings-control">
          <SegmentedToggle v-model="receiptView" class="settings-receipt-toggle" :options="views" :label="t('receiptDisplay')" aria-describedby="receipt-display-hint" />
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
    </section>
    <section v-if="Array.isArray(grants.data.value?.items)" class="settings-section" aria-labelledby="mcp-heading">
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
          <p>{{ t("mcpConnectedOn", { date: connectedOn(grant.created_at) }) }}</p>
        </div>
        <button type="button" class="button secondary settings-install" @click="revoking = grant">{{ t("mcpRevoke") }}</button>
      </div>
      <div v-if="grants.data.value?.items.length === 0" class="settings-row">
        <p class="settings-copy">{{ t("mcpNoApps") }}</p>
      </div>
    </section>
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
.settings-select :deep(.app-select-trigger) { min-height: 44px; border-radius: 8px; }
.settings-install { justify-self: end; min-height: 44px; padding: 10px 16px; border-radius: 8px; font-size: 13px; }
.settings-install svg { width: 18px; height: 18px; }
.settings-install-help { margin: 0 24px 24px; padding: 14px 16px; border-left: 2px solid var(--border-account-panel); border-radius: 0 6px 6px 0; background: var(--surface-account-panel); }
.settings-install-help p { color: var(--muted); font-size: 12px; line-height: 1.8; }
.settings-connector-url { min-width: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; }
.settings-row > p.settings-copy { color: var(--muted); font-size: 12px; }
@media (max-width: 780px) { .settings-row { grid-template-columns: minmax(0, 1fr); align-items: start; gap: 16px; }.settings-install { justify-self: start; } }
@media (max-width: 600px) { .settings-section-heading { padding: 16px; }.settings-row { margin-inline: 16px; padding-block: 20px; }.settings-install-help { margin: 0 16px 20px; }.settings-section + .settings-section { margin-top: 22px; } }
</style>
