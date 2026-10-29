<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { accountName, providerName, SHOPTET_SHOPS } from "../names";
import { ApiError, request } from "../api";
import type { Connection, SyncRun } from "../api";
import { sessionOptions } from "../auth";
import QueryState from "./QueryState.vue";
import LidlLoginForm from "./LidlLoginForm.vue";
import MixitLoginForm from "./MixitLoginForm.vue";
import ShoptetLoginForm from "./ShoptetLoginForm.vue";
import BoltLoginForm from "./BoltLoginForm.vue";
import WoltLoginForm from "./WoltLoginForm.vue";
import GlobusLoginForm from "./GlobusLoginForm.vue";
import FoodoraTokenForm from "./FoodoraTokenForm.vue";
import AppIcon from "./AppIcon.vue";
import AppDialog from "./AppDialog.vue";
import AppSwitch from "./AppSwitch.vue";
import AppSelect from "./AppSelect.vue";
import AppTabs from "./AppTabs.vue";
import IconButton from "./IconButton.vue";
import { syncIntervalOptions } from "../syncIntervals";
import AppButton from "./AppButton.vue";

const props = withDefaults(defineProps<{ connection: Connection; initialLinking?: boolean }>(), { initialLinking: false });
const { t, locale } = useI18n();
const client = useQueryClient();
const expanded = ref(props.initialLinking);
const tab = ref<"sync" | "history" | "manage">("sync");
const tabOptions = computed(() => [
  { value: "sync" as const, label: t("synchronization") },
  { value: "history" as const, label: t("history") },
  { value: "manage" as const, label: t("manageConnection") },
]);
const linking = ref(props.initialLinking);
const name = ref(props.connection.name);
const session = useQuery(sessionOptions);
const interval = ref(props.connection.sync_interval_minutes ?? session.data.value?.default_sync_interval_minutes ?? 360);
const confirm = ref<"clear-data" | "remove" | null>(null);
const pauseSync = ref(true);
const notice = ref("");
const active = computed(() => props.connection.latest_sync?.status === "pending" || props.connection.latest_sync?.status === "running");
const running = computed(() => props.connection.latest_sync?.status === "running");
const linkedAccount = computed(() => ["connected", "error"].includes(props.connection.status));
const statusKey = computed(() => active.value ? `syncStatus_${props.connection.latest_sync?.status}` : ({
  connected: "connectionConnected", not_connected: "connectionNotConnected", disconnected: "connectionDisconnected",
  reauth_required: "connectionReauth", error: "connectionError",
})[props.connection.status] ?? "connectionError");
const statusTone = computed(() => active.value ? "working" : props.connection.status === "connected" ? "connected" : ["error", "reauth_required"].includes(props.connection.status) ? "attention" : "");
watch(() => props.connection.name, (value) => { name.value = value; });
const displayName = computed(() => accountName(props.connection) || providerName(props.connection.provider));
watch(() => props.connection.sync_interval_minutes, (value) => { if (value !== null) interval.value = value; });
const endpoint = computed(() => `/providers/${props.connection.provider}/connections/${props.connection.id}`);

function updateConnection(connection: Connection): void {
  client.setQueryData<{ items: Connection[] }>(["connections"], (previous) => previous ? {
    items: previous.items.map((item) => item.id === connection.id ? connection : item),
  } : previous);
}
function linked(connection: Connection): void { updateConnection(connection); linking.value = false; }
async function refreshData(): Promise<void> {
  await Promise.all([
    client.invalidateQueries({ queryKey: ["overview"] }),
    client.invalidateQueries({ queryKey: ["overview-stats"] }),
    client.invalidateQueries({ queryKey: ["receipts"] }),
  ]);
}
const save = useMutation({
  mutationFn: (values: { name?: string; sync_interval_minutes?: number | null }) => request<Connection>(endpoint.value, { method: "PATCH", body: JSON.stringify(values) }),
  onSuccess: (connection) => { updateConnection(connection); notice.value = "settingsSaved"; },
});
const sync = useMutation({
  mutationFn: () => request<SyncRun>(`${endpoint.value}/sync`, { method: "POST", body: JSON.stringify({ refresh_existing: false }) }),
  onSuccess: async (run) => { updateConnection({ ...props.connection, latest_sync: run }); await client.invalidateQueries({ queryKey: ["connection-runs", props.connection.id] }); },
  onError: () => { void client.invalidateQueries({ queryKey: ["connections"] }); },
});
const manage = useMutation({
  mutationFn: (action: "disconnect" | "clear-data" | "remove") => action === "remove"
    ? request<void>(endpoint.value, { method: "DELETE" }).then(() => null)
    : request<Connection>(`${endpoint.value}/${action}`, { method: "POST", ...(action === "clear-data" ? { body: JSON.stringify({ pause_sync: pauseSync.value }) } : {}) }),
  onSuccess: async (connection, action) => {
    confirm.value = null;
    linking.value = false;
    if (connection) updateConnection(connection);
    else client.setQueryData<{ items: Connection[] }>(["connections"], (previous) => previous ? { items: previous.items.filter((item) => item.id !== props.connection.id) } : previous);
    notice.value = action === "disconnect" ? "accountDisconnected" : "connectionDataDeleted";
    if (action !== "disconnect") await refreshData();
    if (action === "remove") client.removeQueries({ queryKey: ["connection-runs", props.connection.id] });
    else await client.invalidateQueries({ queryKey: ["connection-runs", props.connection.id] });
  },
  onError: () => { void client.invalidateQueries({ queryKey: ["connections"] }); },
});
const busy = computed(() => manage.isPending.value || save.isPending.value || sync.isPending.value);
const managementDisabled = computed(() => running.value || linking.value || busy.value);
const historyOpen = computed(() => expanded.value && tab.value === "history");
const HISTORY_LIMIT = 10;
const historyOffset = ref(0);
const history = useQuery({
  queryKey: computed(() => ["connection-runs", props.connection.id, historyOffset.value]),
  queryFn: ({ signal }) => request<{ items: SyncRun[]; total: number }>(`${endpoint.value}/runs?limit=${HISTORY_LIMIT}&offset=${historyOffset.value}`, { signal }),
  placeholderData: keepPreviousData,
  enabled: historyOpen,
  refetchInterval: () => historyOpen.value && active.value ? 3000 : false,
});
function ask(action: "clear-data" | "remove"): void {
  manage.reset(); pauseSync.value = true; confirm.value = action; notice.value = "";
}
function connect(): void { linking.value = true; notice.value = ""; }
function date(value: string | null): string {
  if (!value) return t("neverSynced");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return t("unknownDate");
  return new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}
function errorKey(code: string | null | undefined): string {
  if (["reauth_required", "connection_not_linked", "authentication_failed", "auth_required"].includes(code ?? "")) return "syncAuthFailed";
  if (code === "connection_busy") return "syncAlreadyRunning";
  if (["api_error", "network_error", "provider_unavailable", "provider_response_invalid", "rate_limited"].includes(code ?? "")) return "syncProviderFailed";
  if (["credentials_unavailable", "credential_key_unavailable", "credential_decryption_failed"].includes(code ?? "")) return "syncCredentialFailed";
  if (code === "account_mismatch" || code === "account_identity_unavailable") return "syncIdentityFailed";
  if (code === "history_incomplete") return "syncIncompleteHistory";
  if (code === "quality_error") return "syncInvalidReceipt";
  if (code === "connection_not_found") return "connectionNotFound";
  if (code === "connection_disconnected") return "accountDisconnected";
  if (code === "data_cleared") return "connectionDataDeleted";
  return "syncFailed";
}
const actionError = computed(() => {
  const error = manage.error.value ?? save.error.value ?? sync.error.value;
  if (!error) return "";
  if (error instanceof ApiError && error.code === "connection_busy") return "connectionBusy";
  if (manage.isError.value) return "connectionActionFailed";
  if (save.isError.value) return "connectionSaveFailed";
  return errorKey(error instanceof ApiError ? error.code : null);
});
</script>

<template>
  <article class="connection-account" :class="{ 'is-expanded': expanded }">
    <div class="account-row">
      <button class="account-toggle" :aria-expanded="expanded" :aria-controls="`account-${connection.id}`" @click="expanded = !expanded">
        <span class="account-identity"><strong>{{ displayName }}</strong><small>{{ t('receiptCount', connection.receipt_count) }} · {{ connection.country }}</small></span>
        <span class="account-status" :class="statusTone"><i />{{ t(statusKey) }}</span>
        <span class="account-fresh"><span class="sr-only">{{ t('lastSuccessfulSync') }}: </span>{{ date(connection.last_synced_at) }}</span>
        <AppIcon name="chevron" :class="{ rotated: expanded }" />
      </button>
      <IconButton class="remove-account" tone="danger" data-action="remove" :disabled="managementDisabled" :label="t('removeNamedConnection', { name: displayName })" :title="t('removeConnection')" @click="ask('remove')"><AppIcon name="trash" /></IconButton>
    </div>
    <div v-show="expanded" :id="`account-${connection.id}`" class="account-panel">
      <AppTabs v-model="tab" class="account-tabs" :options="tabOptions" :label="t('connectionSettings')" :panel-id="`panel-${connection.id}`" />
      <p v-if="actionError && !confirm" role="alert" class="form-alert">{{ t(actionError) }}</p>
      <p v-if="notice" class="account-notice" role="status">{{ t(notice) }}</p>
      <MixitLoginForm v-if="linking && connection.provider === 'mixit'" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <ShoptetLoginForm v-else-if="linking && SHOPTET_SHOPS.includes(connection.provider)" :provider="connection.provider" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <BoltLoginForm v-else-if="linking && connection.provider === 'bolt'" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <WoltLoginForm v-else-if="linking && connection.provider === 'wolt'" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <GlobusLoginForm v-else-if="linking && connection.provider === 'globus'" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <FoodoraTokenForm v-else-if="linking && connection.provider === 'foodora'" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <LidlLoginForm v-else-if="linking && connection.provider === 'kaufland'" provider="kaufland" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <LidlLoginForm v-else-if="linking" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
      <section v-show="tab === 'sync'" :id="`panel-${connection.id}-sync`" role="tabpanel" :aria-labelledby="`panel-${connection.id}-tab-sync`" tabindex="0">
        <div v-if="!linkedAccount && !linking" class="reconnect-note"><p>{{ t(connection.status === 'not_connected' ? 'connectToImport' : 'reconnectToImport') }}</p><AppButton size="sm" :disabled="active || busy" @click="connect">{{ t(connection.status === 'not_connected' ? 'linkAccount' : 'reconnectAccount') }}</AppButton></div>
        <div class="account-setting">
          <div><strong>{{ t('automaticSync') }}</strong><p>{{ t('automaticSyncHelp') }}</p></div>
          <AppSwitch :model-value="connection.sync_interval_minutes !== null" :disabled="!linkedAccount || active || busy || linking" :label="t('automaticSync')" @update:model-value="save.mutate({ sync_interval_minutes: $event ? interval : null })"><span>{{ t(connection.sync_interval_minutes === null ? 'off' : 'on') }}</span></AppSwitch>
        </div>
        <div v-if="connection.sync_interval_minutes !== null" class="account-setting interval-setting">
          <span>{{ t('syncFrequency') }}</span>
          <AppSelect :model-value="String(interval)" :options="syncIntervalOptions(t, interval)" :label="t('syncFrequency')" :disabled="!linkedAccount || active || busy || linking" @update:model-value="interval = Number($event); save.mutate({ sync_interval_minutes: interval })" />
        </div>
        <p v-if="connection.sync_interval_minutes !== null && connection.next_sync_at" class="account-next">{{ t('nextSync') }}: {{ date(connection.next_sync_at) }}</p>
        <div class="account-setting manual-setting"><div><strong>{{ t('manualSync') }}</strong><p>{{ t('manualSyncHelp') }}</p></div><AppButton size="sm" variant="primary" :disabled="!linkedAccount || active || busy || linking" @click="sync.mutate()">{{ t(active || sync.isPending.value ? 'syncing' : 'syncNow') }}</AppButton></div>
        <div v-if="connection.latest_sync" class="sync-progress" role="status" :aria-live="active ? 'polite' : 'off'"><strong>{{ t(`syncStatus_${connection.latest_sync.status}`) }}</strong><p>{{ t('syncCounts', { seen: connection.latest_sync.receipts_seen, imported: connection.latest_sync.receipts_imported, skipped: connection.latest_sync.receipts_skipped, pages: connection.latest_sync.pages_count }) }}</p><p v-if="connection.latest_sync.status === 'failed'" class="sync-error">{{ t(errorKey(connection.latest_sync.error_code)) }}</p></div>
      </section>
      <section v-if="tab === 'history'" :id="`panel-${connection.id}-history`" role="tabpanel" :aria-labelledby="`panel-${connection.id}-tab-history`" tabindex="0">
        <QueryState :pending="history.isPending.value" :failed="history.isError.value" @retry="history.refetch()">
          <p v-if="!history.data.value?.items.length" class="section-note">{{ t('noSyncHistory') }}</p>
          <ol v-else class="sync-history-list">
<li v-for="run in history.data.value?.items" :key="run.id">
            <div class="history-result"><time :datetime="run.created_at">{{ date(run.created_at) }}</time><strong>{{ t(`syncStatus_${run.status}`) }}</strong><small v-if="run.finished_at">{{ t('syncFinished') }}: {{ date(run.finished_at) }}</small></div>
            <div class="history-counts"><span>{{ t('importedCount', { count: run.receipts_imported }) }}</span><span>{{ t('skippedCount', { count: run.receipts_skipped }) }}</span></div>
            <p v-if="run.status === 'failed'" class="sync-error">{{ t(errorKey(run.error_code)) }}</p>
          </li>
</ol>
          <div v-if="(history.data.value?.total ?? 0) > HISTORY_LIMIT" class="pagination">
            <span>{{ t('pageRange', { start: historyOffset + 1, end: Math.min(historyOffset + HISTORY_LIMIT, history.data.value?.total ?? 0), total: history.data.value?.total ?? 0 }) }}</span>
            <div>
              <AppButton type="button" :disabled="historyOffset === 0 || history.isFetching.value" @click="historyOffset = Math.max(0, historyOffset - HISTORY_LIMIT)">{{ t('previous') }}</AppButton>
              <AppButton type="button" :disabled="historyOffset + HISTORY_LIMIT >= (history.data.value?.total ?? 0) || history.isFetching.value" @click="historyOffset += HISTORY_LIMIT">{{ t('next') }}</AppButton>
            </div>
          </div>
        </QueryState>
      </section>
      <section v-if="tab === 'manage'" :id="`panel-${connection.id}-manage`" role="tabpanel" :aria-labelledby="`panel-${connection.id}-tab-manage`" tabindex="0">
        <form class="rename-form" @submit.prevent="save.mutate({ name: name.trim() })"><label :for="`name-${connection.id}`">{{ t('connectionName') }}</label><div><input :id="`name-${connection.id}`" v-model="name" maxlength="120" :disabled="busy" /><AppButton size="sm" type="submit" :disabled="name.trim() === connection.name || busy">{{ t('save') }}</AppButton></div></form>
        <div class="account-setting"><div><strong>{{ t('storeAuthorization') }}</strong><p>{{ t('reconnectHelp') }}</p></div><AppButton v-if="!linking" size="sm" :disabled="active || busy" @click="connect">{{ t(['not_connected', 'disconnected'].includes(connection.status) ? 'linkAccount' : 'reconnectAccount') }}</AppButton></div>
        <div class="account-setting"><div><strong>{{ t('disconnectAccount') }}</strong><p>{{ t('disconnectHelp') }}</p></div><AppButton size="sm" data-action="disconnect" :disabled="managementDisabled || ['not_connected', 'disconnected'].includes(connection.status)" @click="manage.mutate('disconnect')">{{ t('disconnect') }}</AppButton></div>
        <div class="account-setting"><div><strong>{{ t('deleteConnectionData') }}</strong><p>{{ t('clearDataHelp') }}</p></div><AppButton size="sm" class="danger-text" data-action="clear-data" :disabled="managementDisabled" @click="ask('clear-data')">{{ t('deleteData') }}</AppButton></div>
        <p v-if="running" class="section-note">{{ t('waitForSync') }}</p>
      </section>
    </div>
    <AppDialog v-if="confirm" :title="t(confirm === 'remove' ? 'removeConnection' : 'deleteConnectionData')" :busy="manage.isPending.value" @close="confirm = null">
      <p>{{ t(confirm === 'remove' ? 'removeConnectionWarning' : 'clearDataWarning', { name: displayName }) }}</p>
      <p class="deletion-count">{{ t('receiptCount', connection.receipt_count) }} · {{ displayName }}</p>
      <template v-if="confirm === 'clear-data'"><label class="pause-checkbox"><input v-model="pauseSync" type="checkbox" :disabled="manage.isPending.value" />{{ t('pauseAutomaticSync') }}</label><p>{{ t('reimportWarning') }}</p></template>
      <p v-if="actionError" role="alert" class="form-alert">{{ t(actionError) }}</p>
      <div class="dialog-actions"><AppButton data-action="cancel" :disabled="manage.isPending.value" @click="confirm = null">{{ t('cancel') }}</AppButton><AppButton variant="danger" data-action="confirm" :disabled="manage.isPending.value || running" @click="manage.mutate(confirm)">{{ t(manage.isPending.value ? 'deleting' : confirm === 'remove' ? 'removeConnection' : 'deleteData') }}</AppButton></div>
    </AppDialog>
  </article>
</template>

<style scoped>
.connection-account { min-width: 0; }
.account-row { display: grid; grid-template-columns: minmax(0, 1fr) 36px; gap: 8px; align-items: center; padding: 0 10px; border-radius: 7px; transition: background .15s; }
.is-expanded > .account-row { background: var(--bg-inset); }
.account-toggle { display: grid; grid-template-columns: minmax(130px, 1.25fr) minmax(105px, 1fr) minmax(100px, 1fr) 20px; gap: 16px; align-items: center; border: 0; background: transparent; padding: 22px 0; text-align: left; color: inherit; width: 100%; min-width: 0; border-radius: 4px; }
.account-toggle:hover { background: var(--bg-row-hover); }
.account-toggle:focus-visible { outline: 2px solid var(--focus-strong); outline-offset: -3px; border-radius: 6px; }
.account-identity { min-width: 0; overflow-wrap: anywhere; }
.account-identity strong { display: block; font-size: 14px; font-weight: 600; }
.account-identity small { display: block; color: var(--text-muted); font-size: 11px; margin-top: 5px; }
.account-status { display: flex; align-items: center; gap: 7px; font-size: 12px; min-width: 0; }
.account-status i { width: 6px; height: 6px; border-radius: 50%; background: var(--text-subtle); flex-shrink: 0; }
.account-status.connected { color: var(--success); }.account-status.connected i { background: var(--success); }
.account-status.attention { color: var(--warning); }.account-status.attention i { background: var(--warning); }
.account-status.working { color: var(--info); }.account-status.working i { background: var(--info); animation: pulse 1s infinite alternate; }
.account-fresh { color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.account-toggle > svg { width: 20px; height: 20px; transition: transform .16s; color: var(--text-muted); }.account-toggle > svg.rotated { transform: rotate(180deg); }
.account-panel { margin: 0 10px 16px; padding: 0 18px 20px; border-left: 2px solid var(--control); border-radius: 0 0 6px 0; background: var(--bg-inset); animation: arrive .16s ease-out; }
.account-tabs { border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.account-setting { display: flex; gap: 18px; align-items: center; justify-content: space-between; padding: 18px 0; }
.account-setting + .account-setting { border-top: 1px solid var(--border); }
.account-setting > div { min-width: 0; }.account-setting strong { font-size: 13px; font-weight: 550; }.account-setting p { color: var(--text-muted); font-size: 12px; line-height: 1.7; margin-top: 4px; }
.account-setting .button { flex-shrink: 0; }
.interval-setting > span { font-size: 13px; }.interval-setting .app-select { min-width: 170px; max-width: 100%; }
.account-next { color: var(--text-muted); font-size: 12px; line-height: 1.7; margin: -2px 0 16px; }
.manual-setting { border-top: 1px solid var(--border); }
.reconnect-note { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px; margin: 16px 0; background: var(--bg-muted); border-radius: 5px; font-size: 12px; line-height: 1.7; }.reconnect-note .button { flex-shrink: 0; }
.sync-progress { border-left: 2px solid var(--icon-accent); padding: 5px 14px; margin: 16px 0 0; font-size: 12px; }.sync-progress > strong { font-weight: 550; }.sync-progress p { margin-top: 7px; line-height: 1.8; color: var(--text-muted); }.sync-error, .sync-progress .sync-error { color: var(--danger); font-size: 12px; line-height: 1.7; }
.account-notice { color: var(--success); font-size: 12px; margin: 18px 0; }
.sync-history-list { list-style: none; margin: 0; padding: 0; }.sync-history-list li { padding: 17px 0; border-bottom: 1px solid var(--border); display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 16px; font-size: 12px; }.history-result { display: flex; flex-direction: column; gap: 5px; }.history-result strong { font-weight: 500; }.history-result time, .history-result small { color: var(--text-muted); }.history-result small { font-size: 11px; }.history-counts { display: flex; align-items: start; gap: 16px; font-variant-numeric: tabular-nums; color: var(--text-muted); }.sync-history-list .sync-error { grid-column: 1 / -1; }
.rename-form { margin: 18px 0 4px; padding-bottom: 20px; border-bottom: 1px solid var(--border); }.rename-form label { margin: 0 0 10px; }.rename-form > div { display: flex; gap: 10px; }.rename-form input { min-width: 0; flex: 1; }.rename-form .button { flex-shrink: 0; }.danger-text { color: var(--danger); }
@media (max-width: 1000px) { .account-toggle { grid-template-columns: minmax(110px, 1fr) minmax(100px, .9fr) 20px; gap: 12px; }.account-fresh { grid-column: 1 / 3; grid-row: 2; }.account-toggle > svg { grid-column: 3; grid-row: 1 / 3; }.account-toggle { padding: 18px 0; row-gap: 7px; } }
@media (max-width: 600px) { .account-row { padding-inline: 8px; }.account-panel { margin-inline: 8px; padding-inline: 12px; }.remove-account { width: 36px; min-height: 44px; }.account-setting { gap: 14px; }.account-setting > div { flex: 1; }.reconnect-note { align-items: flex-start; flex-direction: column; }.account-setting p { max-width: 260px; }.account-fresh { font-size: 11px; }.account-toggle { grid-template-columns: minmax(0, 1fr) 20px; row-gap: 7px; }.account-status { grid-column: 1; grid-row: 2; }.account-fresh { grid-column: 1; grid-row: 3; }.account-toggle > svg { grid-column: 2; grid-row: 1 / 4; }.history-counts { flex-direction: column; gap: 5px; }.account-setting:has(.danger-text) { align-items: flex-start; }.account-setting:has(.danger-text) .button { margin-top: 1px; } }
@media (max-width: 390px) { .account-setting { flex-wrap: wrap; }.account-setting > div { flex-basis: 145px; }.rename-form > div { flex-wrap: wrap; }.rename-form input { flex-basis: 100%; }.history-counts { gap: 7px; }.account-toggle { padding-right: 4px; } }
</style>
