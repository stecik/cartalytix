<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Connection, Provider } from "../api";
import AppCard from "../components/AppCard.vue";
import AppIcon from "../components/AppIcon.vue";
import AppDialog from "../components/AppDialog.vue";
import QueryState from "../components/QueryState.vue";
import ConnectionAccount from "../components/ConnectionAccount.vue";
import { providerLogos } from "../providerLogos";
import { matches } from "../search";
import AppButton from "../components/AppButton.vue";

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
const addTitles: Record<string, string> = { lidl: "addLidlAccount", mixit: "addMixitAccount", fermato: "addFermatoAccount", nejkafe: "addNejkafeAccount", bolt: "addBoltAccount", albert: "addAlbertAccount", wolt: "addWoltAccount", kaufland: "addKauflandAccount", globus: "addGlobusAccount", foodora: "addFoodoraAccount" };
function openAdd(provider: Provider): void {
  if (provider.can_connect === false) return;
  name.value = "";
  createConnection.reset();
  adding.value = provider.code;
}
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
      <AppCard v-for="provider in connected" :key="provider.code" class="connected-store" :aria-labelledby="`store-${provider.code}`">
        <template #header>
          <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" class="provider-logo" width="38" height="38" alt="" />
          <div class="store-copy">
            <h2 :id="`store-${provider.code}`">{{ label(provider) }}</h2>
            <p>{{ t('accountCount', accounts(provider.code).length) }}</p>
            <p v-if="provider.code === 'albert' && provider.can_connect === false">{{ t("albertCredentialsUnavailable") }}</p>
          </div>
          <AppButton v-if="addTitles[provider.code]" size="sm" class="add-account" :disabled="provider.can_connect === false" @click="openAdd(provider)"><AppIcon name="plus" />{{ t("addAccount") }}</AppButton>
        </template>
        <ul class="account-list">
          <li v-for="connection in accounts(provider.code)" :key="connection.id">
            <ConnectionAccount v-if="addTitles[connection.provider]" :connection="connection" :initial-linking="connection.id === createdId" :can-connect="provider.can_connect !== false" />
          </li>
        </ul>
      </AppCard>
      <AppCard v-if="unconnected.length || upcoming.length || searching" padded class="store-picker" aria-labelledby="store-picker-title">
        <h2 id="store-picker-title">{{ t(connected.length ? "addStore" : "addFirstStore") }}</h2>
        <p v-if="!connected.length" class="store-picker-intro">{{ t("noConnectionsDescription") }}</p>
        <label class="store-search"><span class="sr-only">{{ t("searchStores") }}</span><input v-model="search" data-search="stores" type="search" maxlength="100" :placeholder="t('searchStores')" /></label>
        <h3 v-if="!searching && offered.length" class="store-picker-subtitle">{{ t("popularStores") }}</h3>
        <ul v-if="offered.length" class="store-options">
          <li v-for="provider in offered" :key="provider.code" class="store-option">
            <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" width="38" height="30" alt="" />
            <div class="store-copy">
              <span>{{ label(provider) }}</span>
              <p v-if="provider.code === 'albert' && provider.can_connect === false">{{ t("albertCredentialsUnavailable") }}</p>
            </div>
            <AppButton v-if="addTitles[provider.code]" size="sm" class="add-account" :disabled="provider.can_connect === false" @click="openAdd(provider)"><AppIcon name="plus" />{{ t("addAccount") }}</AppButton>
          </li>
        </ul>
        <p v-else-if="searching && !upcoming.length" class="section-note" role="status">{{ t("noStoresFound") }}</p>
      </AppCard>
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
        <div class="dialog-actions"><AppButton data-action="cancel" type="button" :disabled="createConnection.isPending.value" @click="adding = null">{{ t("cancel") }}</AppButton><AppButton variant="primary" type="submit" :disabled="createConnection.isPending.value">{{ t(createConnection.isPending.value ? "saving" : "continueConnecting") }}</AppButton></div>
      </form>
    </AppDialog>
  </div>
</template>

<style>
.connections-workspace { max-width: 1040px; }
.connections-title { margin-bottom: 34px; }
.connections-title h1 { font-size: 29px; line-height: 1.25; font-weight: 600; letter-spacing: -1px; margin-bottom: 9px; }
.connections-title p { color: var(--text-muted); font-size: 13px; line-height: 1.7; }
.provider-logo { width: 38px; height: 38px; object-fit: contain; }
.store-copy { flex: 1; min-width: 0; }
.store-copy p { color: var(--text-muted); font-size: 12px; margin-top: 3px; }
.add-account.button { white-space: nowrap; background: var(--bg-raised); }
.add-account svg { width: 16px; height: 16px; }
.account-list, .upcoming-stores ul { list-style: none; margin: 0; padding: 0; }
.account-list { padding: 6px 14px; }
.account-list > li + li { border-top: 1px solid var(--border); }
.accounts-empty { padding: 24px 22px; font-size: 13px; line-height: 1.8; color: var(--text-muted); }
.store-picker h2 { font-size: 17px; font-weight: 650; letter-spacing: -.3px; }
.store-picker-intro { color: var(--text-muted); font-size: 13px; line-height: 1.8; margin-top: 6px; }
.store-search { display: block; margin-top: 14px; }
.store-search input { width: 100%; }
.store-picker-subtitle { font-size: 12px; font-weight: 600; color: var(--text-muted); margin-top: 18px; }
.store-options { list-style: none; margin: 8px 0 0; padding: 0; }
.store-option { display: flex; align-items: center; gap: 14px; padding: 12px 0; border-top: 1px solid var(--border); font-size: 13px; }
.store-option img { object-fit: contain; flex-shrink: 0; }
.store-option .store-copy span { font-weight: 500; }
.store-picker .section-note { margin-top: 14px; }
.upcoming-stores { margin-top: 32px; padding: 20px 22px 6px; border: 1px dashed var(--border-card); border-radius: 12px; }
.upcoming-stores h2 { font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 12px; }
.upcoming-store { display: flex; align-items: center; gap: 14px; padding: 14px 0; border-top: 1px solid var(--border); font-size: 13px; }
.upcoming-store img { object-fit: contain; flex-shrink: 0; }
.upcoming-store span { flex: 1; font-weight: 500; }
.upcoming-store small { color: var(--text-muted); font-size: 12px; }
.connection-form { max-width: 460px; margin: 16px 0 0; }
.connection-form label { margin-top: 18px; }
.connection-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
@media (max-width: 600px) { .account-list { padding: 4px 8px; }.accounts-empty { padding: 22px 16px; }.upcoming-stores { padding: 18px 16px 4px; } }
@media (max-width: 390px) { .add-account.button { padding: 8px; gap: 4px; }.provider-logo { width: 32px; height: 32px; } }
</style>
