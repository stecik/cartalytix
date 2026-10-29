<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Connection, Provider } from "../api";
import AppIcon from "../components/AppIcon.vue";
import AppDialog from "../components/AppDialog.vue";
import QueryState from "../components/QueryState.vue";
import ConnectionAccount from "../components/ConnectionAccount.vue";
import { providerLogos } from "../providerLogos";
import { matches } from "../search";

const { t, locale } = useI18n();
const client = useQueryClient();
const adding = ref<string | null>(null);
const name = ref("");
const createdId = ref<string | null>(null);
const connections = useQuery({
  queryKey: ["connections"],
  queryFn: ({ signal }) => request<{ items: Connection[] }>("/connections", { signal }),
  refetchInterval: (query) => query.state.data?.items.some((connection) =>
    connection.latest_sync?.status === "pending" || connection.latest_sync?.status === "running",
  ) ? 3000 : false,
});
watch(() => connections.data.value?.items, (current, previous) => {
  const latestById = new Map(current?.map((connection) => [connection.id, connection.latest_sync]));
  const completed = previous?.filter((connection) => {
    const wasActive = connection.latest_sync?.status === "pending" || connection.latest_sync?.status === "running";
    const latest = latestById.get(connection.id);
    return wasActive && latest && latest.status !== "pending" && latest.status !== "running";
  });
  if (completed?.length) {
    void client.invalidateQueries({ queryKey: ["overview"] });
    void client.invalidateQueries({ queryKey: ["overview-stats"] });
    void client.invalidateQueries({ queryKey: ["receipts"] });
    for (const connection of completed) void client.invalidateQueries({ queryKey: ["connection-runs", connection.id] });
  }
});
const providers = useQuery({
  queryKey: ["providers"],
  queryFn: ({ signal }) => request<{ items: Provider[] }>("/providers", { signal }),
});
const POPULAR_LIMIT = 5;
const search = ref("");
const label = (provider: Provider): string => provider.code === "lidl" ? t("lidlPlus") : provider.name;
const collator = computed(() => new Intl.Collator(locale.value));
const sorted = computed(() => [...providers.data.value?.items ?? []].sort((left, right) => collator.value.compare(label(left), label(right))));
const accountsByProvider = computed(() => {
  const grouped = new Map<string, Connection[]>();
  for (const connection of connections.data.value?.items ?? []) grouped.set(connection.provider, [...grouped.get(connection.provider) ?? [], connection]);
  return grouped;
});
const accounts = (provider: string): Connection[] => accountsByProvider.value.get(provider) ?? [];
const available = computed(() => sorted.value.filter((provider) => provider.status === "available"));
const connected = computed(() => available.value.filter((provider) => accounts(provider.code).length));
const unconnected = computed(() => available.value.filter((provider) => !accounts(provider.code).length));
const searching = computed(() => search.value.trim() !== "");
const offered = computed(() => searching.value
  ? unconnected.value.filter((provider) => matches(label(provider), search.value))
  : [...unconnected.value].sort((left, right) => right.connection_count - left.connection_count).slice(0, POPULAR_LIMIT));
const upcoming = computed(() => sorted.value.filter((provider) => provider.status !== "available" && matches(label(provider), search.value)));
const addTitles: Record<string, string> = { lidl: "addLidlAccount", mixit: "addMixitAccount", fermato: "addFermatoAccount", nejkafe: "addNejkafeAccount", bolt: "addBoltAccount", wolt: "addWoltAccount", kaufland: "addKauflandAccount", globus: "addGlobusAccount", foodora: "addFoodoraAccount" };
function openAdd(provider: string): void { name.value = ""; createConnection.reset(); adding.value = provider; }
const createConnection = useMutation({
  mutationFn: (provider: string) => request<Connection>(`/providers/${provider}/connections`, {
    method: "POST", body: JSON.stringify({ name: name.value.trim() }),
  }),
  onSuccess: async (connection) => {
    adding.value = null;
    name.value = "";
    createdId.value = connection.id;
    client.setQueryData<{ items: Connection[] }>(["connections"], (previous) => ({ items: [...(previous?.items ?? []), connection] }));
    await client.invalidateQueries({ queryKey: ["overview"] });
  },
});
</script>

<template>
  <div class="view-content connections-workspace">
    <header class="connections-title"><h1>{{ t("connections") }}</h1><p>{{ t("connectionIntro") }}</p></header>
    <QueryState :pending="providers.isPending.value || connections.isPending.value" :failed="providers.isError.value || connections.isError.value" @retry="providers.refetch(); connections.refetch()">
      <section v-for="provider in connected" :key="provider.code" class="connected-store" :aria-labelledby="`store-${provider.code}`">
        <header class="store-header">
          <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" class="provider-logo" width="38" height="38" alt="" />
          <div class="store-copy"><h2 :id="`store-${provider.code}`">{{ label(provider) }}</h2><p>{{ t('accountCount', accounts(provider.code).length) }}</p></div>
          <button v-if="addTitles[provider.code]" class="button secondary add-account" @click="openAdd(provider.code)"><AppIcon name="plus" />{{ t("addAccount") }}</button>
        </header>
        <ul class="account-list">
          <li v-for="connection in accounts(provider.code)" :key="connection.id">
            <ConnectionAccount v-if="addTitles[connection.provider]" :connection="connection" :initial-linking="connection.id === createdId" />
          </li>
        </ul>
      </section>
      <section v-if="unconnected.length || upcoming.length || searching" class="store-picker" aria-labelledby="store-picker-title">
        <h2 id="store-picker-title">{{ t(connected.length ? "addStore" : "addFirstStore") }}</h2>
        <p v-if="!connected.length" class="store-picker-intro">{{ t("noConnectionsDescription") }}</p>
        <label class="store-search"><span class="sr-only">{{ t("searchStores") }}</span><input v-model="search" data-search="stores" type="search" maxlength="100" :placeholder="t('searchStores')" /></label>
        <h3 v-if="!searching && offered.length" class="store-picker-subtitle">{{ t("popularStores") }}</h3>
        <ul v-if="offered.length" class="store-options">
          <li v-for="provider in offered" :key="provider.code" class="store-option">
            <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" width="38" height="30" alt="" />
            <span>{{ label(provider) }}</span>
            <button v-if="addTitles[provider.code]" class="button secondary add-account" @click="openAdd(provider.code)"><AppIcon name="plus" />{{ t("addAccount") }}</button>
          </li>
        </ul>
        <p v-else-if="searching && !upcoming.length" class="section-note" role="status">{{ t("noStoresFound") }}</p>
      </section>
      <section v-if="upcoming.length" class="upcoming-stores" :aria-label="t('otherStores')">
        <h2>{{ t("otherStores") }}</h2>
        <ul>
<li v-for="provider in upcoming" :key="provider.code" class="upcoming-store">
          <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" width="38" height="30" alt="" />
          <span>{{ label(provider) }}</span><small>{{ t("comingLater") }}</small>
        </li>
</ul>
      </section>
      <p v-if="!providers.data.value?.items.length" class="section-note">{{ t("noProviders") }}</p>
    </QueryState>
    <AppDialog v-if="adding" :title="t(addTitles[adding] ?? 'addAccount')" :busy="createConnection.isPending.value" @close="adding = null">
      <p>{{ t("accountNameHelp") }}</p>
      <form :aria-busy="createConnection.isPending.value" @submit.prevent="adding && createConnection.mutate(adding)">
        <label for="connection-name">{{ t("connectionName") }}</label>
        <input id="connection-name" v-model="name" name="connection-name" maxlength="120" :disabled="createConnection.isPending.value" />
        <p v-if="createConnection.isError.value" class="form-alert" role="alert">{{ t("connectionSaveFailed") }}</p>
        <div class="dialog-actions"><button class="button secondary" data-action="cancel" type="button" :disabled="createConnection.isPending.value" @click="adding = null">{{ t("cancel") }}</button><button class="button primary" type="submit" :disabled="createConnection.isPending.value">{{ t(createConnection.isPending.value ? "saving" : "continueConnecting") }}</button></div>
      </form>
    </AppDialog>
  </div>
</template>

<style>
.connections-workspace { --line: var(--line-connections); max-width: 1040px; }
.connections-title { margin-bottom: 34px; }
.connections-title h1 { font-size: 29px; line-height: 1.25; font-weight: 600; letter-spacing: -1px; margin-bottom: 9px; }
.connections-title p { color: var(--muted); font-size: 13px; line-height: 1.7; }
.connected-store { overflow: hidden; border: 1px solid var(--border-store); border-radius: 12px; background: var(--surface-store); }
.connected-store + .connected-store { margin-top: 28px; }
.store-header { display: flex; align-items: center; gap: 13px; padding: 20px 22px; border-bottom: 1px solid var(--border-store-header); background: var(--surface-store-header); }
.provider-logo { width: 38px; height: 38px; object-fit: contain; }
.store-copy { flex: 1; min-width: 0; }
.store-copy h2 { font-size: 17px; font-weight: 650; letter-spacing: -.3px; }
.store-copy p { color: var(--muted); font-size: 12px; margin-top: 3px; }
.add-account { gap: 6px; white-space: nowrap; padding: 8px 12px; min-height: 38px; background: var(--surface-store); }
.add-account svg { width: 16px; height: 16px; }
.account-list, .upcoming-stores ul { list-style: none; margin: 0; padding: 0; }
.account-list { padding: 6px 14px; }
.account-list > li + li { border-top: 1px solid var(--line); }
.accounts-empty { padding: 24px 22px; font-size: 13px; line-height: 1.8; color: var(--muted); }
.store-picker { padding: 20px 22px; border: 1px solid var(--border-store); border-radius: 12px; background: var(--surface-store); }
.connected-store + .store-picker { margin-top: 32px; }
.store-picker h2 { font-size: 17px; font-weight: 650; letter-spacing: -.3px; }
.store-picker-intro { color: var(--muted); font-size: 13px; line-height: 1.8; margin-top: 6px; }
.store-search { display: block; margin-top: 14px; }
.store-search input { height: 40px; width: 100%; }
.store-picker-subtitle { font-size: 12px; font-weight: 600; color: var(--muted); margin-top: 18px; }
.store-options { list-style: none; margin: 8px 0 0; padding: 0; }
.store-option { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-top: 1px solid var(--line); font-size: 13px; }
.store-option img { object-fit: contain; flex-shrink: 0; }
.store-option span { flex: 1; min-width: 0; font-weight: 500; }
.store-picker .section-note { margin-top: 14px; }
.upcoming-stores { margin-top: 32px; padding: 20px 22px 6px; border: 1px dashed var(--border-store); border-radius: 12px; }
.upcoming-stores h2 { font-size: 13px; font-weight: 600; color: var(--muted); margin-bottom: 12px; }
.upcoming-store { display: flex; align-items: center; gap: 14px; padding: 14px 0; border-top: 1px solid var(--line); font-size: 13px; }
.upcoming-store img { object-fit: contain; flex-shrink: 0; }
.upcoming-store span { flex: 1; font-weight: 500; }
.upcoming-store small { color: var(--muted); font-size: 12px; }
.connection-form { max-width: 460px; margin: 16px 0 0; }
.connection-form label { margin-top: 18px; }
.connection-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
@media (max-width: 600px) { .store-header { padding: 16px; }.account-list { padding: 4px 8px; }.accounts-empty { padding: 22px 16px; }.upcoming-stores { padding: 18px 16px 4px; }.store-picker { padding: 18px 16px; }.connected-store + .connected-store { margin-top: 22px; } }
@media (max-width: 390px) { .store-header { gap: 10px; padding: 14px 12px; }.store-copy h2 { font-size: 15px; }.add-account { padding: 8px; font-size: 12px; gap: 4px; }.provider-logo { width: 32px; height: 32px; } }
</style>
