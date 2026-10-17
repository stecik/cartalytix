<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { ApiError, request } from "../api";
import type { Connection, SyncRun } from "../api";
import QueryState from "./QueryState.vue";
import LidlLoginForm from "./LidlLoginForm.vue";
import lidlLogo from "../assets/lidl-logo.svg";

const props = defineProps<{ connection: Connection }>();
const { t, locale } = useI18n();
const client = useQueryClient();
const editing = ref(false);
const historyOpen = ref(false);
const linking = ref(false);
const name = ref("");
const interval = ref<number | null>(null);
const active = computed(() => props.connection.latest_sync?.status === "pending" || props.connection.latest_sync?.status === "running");
const statusKey = computed(() => ({
  connected: "connectionConnected", not_connected: "connectionNotConnected",
  reauth_required: "connectionReauth", error: "connectionError",
})[props.connection.status] ?? "connectionError");

function updateConnection(connection: Connection): void {
  client.setQueryData<{ items: Connection[] }>(["connections"], (previous) => previous ? {
    items: previous.items.map((item) => item.id === connection.id ? connection : item),
  } : previous);
}

function linked(connection: Connection): void {
  updateConnection(connection);
  linking.value = false;
}

const save = useMutation({
  mutationFn: () => request<Connection>(`/providers/lidl/connections/${props.connection.id}`, {
    method: "PATCH", body: JSON.stringify({ name: name.value.trim(), sync_interval_minutes: interval.value }),
  }),
  onSuccess: (connection) => { updateConnection(connection); editing.value = false; },
});
const sync = useMutation({
  mutationFn: () => request<SyncRun>(`/providers/lidl/connections/${props.connection.id}/sync`, {
    method: "POST", body: JSON.stringify({ refresh_existing: false }),
  }),
  onSuccess: async (run) => {
    updateConnection({ ...props.connection, latest_sync: run });
    await client.invalidateQueries({ queryKey: ["connection-runs", props.connection.id] });
  },
  onError: () => { void client.invalidateQueries({ queryKey: ["connections"] }); },
});
const history = useQuery({
  queryKey: computed(() => ["connection-runs", props.connection.id]),
  queryFn: ({ signal }) => request<{ items: SyncRun[] }>(`/providers/lidl/connections/${props.connection.id}/runs?limit=10`, { signal }),
  enabled: historyOpen,
  refetchInterval: () => historyOpen.value && active.value ? 3000 : false,
});

function edit(): void {
  name.value = props.connection.name;
  interval.value = props.connection.sync_interval_minutes;
  save.reset();
  editing.value = true;
}

function date(value: string | null): string {
  if (!value) return t("neverSynced");
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return t("unknownDate");
  return new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", {
    dateStyle: "medium", timeStyle: "short",
  }).format(parsed);
}

function errorKey(code: string | null | undefined): string {
  if (code === "reauth_required" || code === "connection_not_linked" || code === "authentication_failed" || code === "auth_required") return "syncAuthFailed";
  if (code === "connection_busy") return "syncAlreadyRunning";
  if (["api_error", "network_error", "provider_unavailable", "provider_response_invalid", "rate_limited"].includes(code ?? "")) return "syncProviderFailed";
  if (["credentials_unavailable", "credential_key_unavailable", "credential_decryption_failed"].includes(code ?? "")) return "syncCredentialFailed";
  if (code === "account_mismatch" || code === "account_identity_unavailable") return "syncIdentityFailed";
  if (code === "history_incomplete") return "syncIncompleteHistory";
  if (code === "quality_error") return "syncInvalidReceipt";
  if (code === "connection_not_found") return "connectionNotFound";
  return "syncFailed";
}

function schedule(value: number | null): string {
  return value === null ? t("manualOnly") : t("everyMinutes", { count: value });
}
</script>

<template>
  <article class="connection-account">
    <div class="connection-heading">
      <div class="connection-identity">
        <img :src="lidlLogo" class="store-logo" width="44" height="44" alt="" aria-hidden="true" />
        <div><h3>{{ connection.name }}</h3><p class="section-note">{{ connection.provider }} · {{ connection.country }}</p></div>
      </div>
      <span class="status-label">{{ t(statusKey) }}</span>
    </div>
    <dl class="connection-facts">
      <div><dt>{{ t("lastSuccessfulSync") }}</dt><dd>{{ date(connection.last_synced_at) }}</dd></div>
      <div><dt>{{ t("syncSchedule") }}</dt><dd>{{ schedule(connection.sync_interval_minutes) }}</dd></div>
      <div v-if="connection.next_sync_at"><dt>{{ t("nextSync") }}</dt><dd>{{ date(connection.next_sync_at) }}</dd></div>
    </dl>
    <div v-if="connection.latest_sync" class="sync-progress" role="status" :aria-live="active ? 'polite' : 'off'">
      <strong>{{ t(`syncStatus_${connection.latest_sync.status}`) }}</strong>
      <p>{{ t("syncCounts", { seen: connection.latest_sync.receipts_seen, imported: connection.latest_sync.receipts_imported, skipped: connection.latest_sync.receipts_skipped, pages: connection.latest_sync.pages_count }) }}</p>
      <p v-if="connection.latest_sync.status === 'failed'" class="form-alert">{{ t(errorKey(connection.latest_sync.error_code)) }}</p>
    </div>
    <p v-if="sync.isError.value" role="alert" class="form-alert">{{ t(errorKey(sync.error.value instanceof ApiError ? sync.error.value.code : null)) }}</p>
    <div class="connection-actions">
      <button v-if="!linking" class="button secondary" :disabled="active" :aria-expanded="linking" @click="linking = true">{{ t(connection.status === 'not_connected' ? "linkAccount" : "reconnectAccount") }}</button>
      <button class="button primary" :disabled="!['connected', 'error'].includes(connection.status) || active || linking || sync.isPending.value" @click="sync.mutate()">{{ t(active || sync.isPending.value ? "syncing" : "syncNow") }}</button>
      <button class="button secondary" @click="edit">{{ t("connectionSettings") }}</button>
      <button class="button secondary" :aria-expanded="historyOpen" @click="historyOpen = !historyOpen">{{ t("syncHistory") }}</button>
    </div>
    <LidlLoginForm v-if="linking" :connection-id="connection.id" @connected="linked" @cancel="linking = false" />
    <form v-if="editing" class="connection-form" :aria-busy="save.isPending.value" @submit.prevent="name.trim() && save.mutate()">
      <label :for="`name-${connection.id}`">{{ t("connectionName") }}</label>
      <input :id="`name-${connection.id}`" v-model="name" maxlength="120" required :disabled="save.isPending.value" />
      <label :for="`interval-${connection.id}`">{{ t("syncSchedule") }}</label>
      <select :id="`interval-${connection.id}`" v-model="interval" :disabled="save.isPending.value">
        <option :value="null">{{ t("manualOnly") }}</option>
        <option :value="60">{{ t("everyHour") }}</option>
        <option :value="360">{{ t("everySixHours") }}</option>
        <option :value="1440">{{ t("everyDay") }}</option>
        <option v-if="interval !== null && ![60, 360, 1440].includes(interval)" :value="interval">{{ schedule(interval) }}</option>
      </select>
      <p v-if="save.isError.value" class="form-alert" role="alert">{{ t("connectionSaveFailed") }}</p>
      <div class="connection-actions">
        <button type="submit" class="button primary" :disabled="!name.trim() || save.isPending.value">{{ t(save.isPending.value ? "saving" : "save") }}</button>
        <button type="button" class="button secondary" :disabled="save.isPending.value" @click="editing = false">{{ t("cancel") }}</button>
      </div>
    </form>
    <div v-if="historyOpen" class="sync-history">
      <QueryState :pending="history.isPending.value" :failed="history.isError.value" @retry="history.refetch()">
        <p v-if="!history.data.value?.items.length" class="section-note">{{ t("noSyncHistory") }}</p>
        <ol v-else class="sync-history-list">
          <li v-for="run in history.data.value?.items" :key="run.id">
            <time :datetime="run.created_at">{{ date(run.created_at) }}</time>
            <strong>{{ t(`syncStatus_${run.status}`) }}</strong>
            <p>{{ t("syncCounts", { seen: run.receipts_seen, imported: run.receipts_imported, skipped: run.receipts_skipped, pages: run.pages_count }) }}</p>
            <p v-if="run.status === 'failed'" class="form-alert">{{ t(errorKey(run.error_code)) }}</p>
          </li>
        </ol>
      </QueryState>
    </div>
  </article>
</template>

<style scoped>
.connection-account { width: 100%; min-width: 0; }
.connection-heading { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.connection-identity { display: flex; align-items: center; gap: 14px; min-width: 0; }
.connection-identity > div { min-width: 0; overflow-wrap: anywhere; }
.connection-heading h3 { font-size: 16px; font-weight: 600; }
.connection-facts { display: flex; flex-wrap: wrap; gap: 18px 36px; margin: 22px 0; font-size: 12px; }
.connection-facts dt { color: var(--muted); margin-bottom: 7px; }
.connection-facts dd { margin: 0; }
.sync-progress { border-left: 2px solid #879b85; padding: 4px 14px; margin: 20px 0; font-size: 12px; }
.sync-progress p { margin-top: 8px; line-height: 1.7; color: var(--muted); }
.sync-history { margin-top: 20px; }
.sync-history-list { list-style: none; padding: 0; }
.sync-history-list li { display: block; padding: 16px 0; font-size: 12px; line-height: 1.8; }
.sync-history-list time { color: var(--muted); margin-right: 16px; }
</style>
