<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Connection, Provider } from "../api";
import AppIcon from "../components/AppIcon.vue";
import QueryState from "../components/QueryState.vue";
import LidlConnection from "../components/LidlConnection.vue";
import lidlLogo from "../assets/lidl-logo.svg";
import albertLogo from "../assets/albert-logo.png";
import globusLogo from "../assets/globus-logo.png";
import rohlikLogo from "../assets/rohlik-logo.png";
import kosikLogo from "../assets/kosik-logo.png";
import billaLogo from "../assets/billa-logo.png";

const providerLogos: Record<string, string> = {
  lidl: lidlLogo, albert: albertLogo, globus: globusLogo,
  rohlik: rohlikLogo, kosik: kosikLogo, billa: billaLogo,
};

const { t } = useI18n();
const client = useQueryClient();
const adding = ref(false);
const name = ref("");
const interval = ref<number | null>(null);
const connections = useQuery({
  queryKey: ["connections"],
  queryFn: ({ signal }) =>
    request<{ items: Connection[] }>("/connections", { signal }),
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
    void client.invalidateQueries({ queryKey: ["receipts"] });
    for (const connection of completed) {
      void client.invalidateQueries({ queryKey: ["connection-runs", connection.id] });
    }
  }
});
const providers = useQuery({
  queryKey: ["providers"],
  queryFn: ({ signal }) =>
    request<{ items: Provider[] }>("/providers", { signal }),
});
const lidlAvailable = computed(() => providers.data.value?.items.some((provider) => provider.code === "lidl" && provider.status === "available"));
const createConnection = useMutation({
  mutationFn: () => request<Connection>("/providers/lidl/connections", {
    method: "POST", body: JSON.stringify({ name: name.value.trim(), sync_interval_minutes: interval.value }),
  }),
  onSuccess: async () => {
    adding.value = false;
    name.value = "";
    interval.value = null;
    await client.invalidateQueries({ queryKey: ["connections"] });
  },
});
</script>

<template>
  <div class="view-content">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("connections") }}</h1>
      <p>{{ t("connectionIntro") }}</p>
    </header>
    <section class="connections-section">
      <div class="section-heading">
        <h2>{{ t("yourConnections") }}</h2>
        <button v-if="lidlAvailable" class="button primary" @click="adding = !adding">{{ t("addLidlAccount") }}</button>
      </div>
      <form v-if="adding" class="connection-form" :aria-busy="createConnection.isPending.value" @submit.prevent="name.trim() && createConnection.mutate()">
        <label for="connection-name">{{ t("connectionName") }}</label>
        <input id="connection-name" v-model="name" name="connection-name" maxlength="120" required :disabled="createConnection.isPending.value" />
        <label for="connection-interval">{{ t("syncSchedule") }}</label>
        <select id="connection-interval" v-model="interval" :disabled="createConnection.isPending.value">
          <option :value="null">{{ t("manualOnly") }}</option>
          <option :value="60">{{ t("everyHour") }}</option>
          <option :value="360">{{ t("everySixHours") }}</option>
          <option :value="1440">{{ t("everyDay") }}</option>
        </select>
        <p v-if="createConnection.isError.value" class="form-alert" role="alert">{{ t("connectionSaveFailed") }}</p>
        <div class="connection-actions">
          <button class="button primary" type="submit" :disabled="!name.trim() || createConnection.isPending.value">{{ t(createConnection.isPending.value ? "saving" : "createConnection") }}</button>
          <button class="button secondary" type="button" :disabled="createConnection.isPending.value" @click="adding = false">{{ t("cancel") }}</button>
        </div>
      </form>
      <QueryState
        :pending="connections.isPending.value"
        :failed="connections.isError.value"
        @retry="connections.refetch()"
      >
        <div
          v-if="connections.data.value?.items.length === 0"
          class="connection-empty"
        >
          <AppIcon name="connections" />
          <div>
            <h3>{{ t("noConnections") }}</h3>
            <p>{{ t("noConnectionsDescription") }}</p>
          </div>
        </div>
        <ul v-else class="connection-list">
          <li
            v-for="connection in connections.data.value?.items"
            :key="connection.id"
          >
            <LidlConnection v-if="connection.provider === 'lidl'" :connection="connection" />
            <template v-else><strong>{{ connection.name }}</strong><span>{{ connection.provider }}</span></template>
          </li>
        </ul>
      </QueryState>
    </section>
    <section class="providers-section">
      <div class="section-heading">
        <h2>{{ t("providerDirectory") }}</h2>
      </div>
      <p class="section-note">{{ t("providerDirectoryNote") }}</p>
      <QueryState
        :pending="providers.isPending.value"
        :failed="providers.isError.value"
        @retry="providers.refetch()"
      >
        <p v-if="providers.data.value?.items.length === 0" class="section-note">
          {{ t("noProviders") }}
        </p>
        <ul v-else class="provider-list">
          <li
            v-for="provider in providers.data.value?.items"
            :key="provider.code"
          >
            <img v-if="providerLogos[provider.code]" :src="providerLogos[provider.code]" class="store-logo" width="44" height="44" alt="" aria-hidden="true" />
            <span v-else class="store-monogram" aria-hidden="true">{{
              provider.name.slice(0, 1)
            }}</span>
            <div class="provider-name">
              <h3>{{ provider.name }}</h3>
              <span>{{ t(provider.status === "available" ? "connectorAvailable" : "connectorPending") }}</span>
            </div>
            <span class="status-label"><i />{{
                t(provider.status === "available" ? "available" : provider.status === "research" ? "research" : "notAvailable")
              }}</span>
            <button
              class="button secondary unavailable-action"
              :disabled="provider.code !== 'lidl' || provider.status !== 'available'"
              :aria-label="t(provider.code === 'lidl' && provider.status === 'available' ? 'addLidlAccount' : 'connectionUnavailable')"
              @click="adding = true"
            >
              {{ t(provider.code === 'lidl' && provider.status === 'available' ? "addLidlAccount" : "notAvailable") }}
            </button>
          </li>
        </ul>
      </QueryState>
    </section>
  </div>
</template>

<style>
.connection-form { max-width: 520px; margin: 24px 0; padding: 0 0 24px; border-bottom: 1px solid var(--line); }
.connection-form label { margin-top: 18px; }
.connection-form select { width: 100%; height: 49px; padding: 10px 13px; border: 1px solid #cbd1c3; border-radius: 4px; background: #fbfbf6; color: inherit; font: inherit; font-size: 13px; }
.connection-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }
.connections-section .section-heading { flex-wrap: wrap; gap: 14px; }
</style>
