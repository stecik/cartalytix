<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { accountOptions } from "../names";
import AppSelect from "../components/AppSelect.vue";
import { request } from "../api";
import type { Connection, ItemPage } from "../api";
import ItemLedger from "../components/ItemLedger.vue";
import type { ItemSortField } from "../components/ItemLedger.vue";
import QueryState from "../components/QueryState.vue";
import ReceiptSearch from "../components/ReceiptSearch.vue";

type QueryKey = "q" | "from" | "to" | "connection" | "min" | "max" | "sort" | "offset";

const DEFAULT_SORT = "receipt_count_desc";
const FILTER_KEYS = ["q", "from", "to", "connection", "min", "max"] as const;
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const limit = 30;

function queryValue(key: QueryKey): string {
  const value = route.query[key];
  return typeof value === "string" ? value : "";
}

function updateQuery(patch: Partial<Record<QueryKey, string>>): void {
  const query: Record<string, string> = {};
  for (const [key, value] of Object.entries({ ...route.query, offset: undefined, ...patch })) {
    if (typeof value === "string" && value !== "") query[key] = value;
  }
  void router.replace({ query });
}

function inputValue(event: Event): string {
  return event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement
    ? event.target.value.trim()
    : "";
}

function dayStart(value: string, days = 0): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return "";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

const offset = computed(() => {
  const value = Number(queryValue("offset"));
  return Number.isInteger(value) && value > 0 ? value : 0;
});
const sort = computed(() => queryValue("sort") || DEFAULT_SORT);
const filtered = computed(() => FILTER_KEYS.some((key) => queryValue(key) !== ""));
const apiPath = computed(() => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset.value) });
  const values = {
    q: queryValue("q").trim(),
    purchased_from: dayStart(queryValue("from")),
    purchased_to: dayStart(queryValue("to"), 1),
    connection_id: queryValue("connection"),
    price_min: queryValue("min"),
    price_max: queryValue("max"),
    sort: sort.value,
  };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return `/items?${params.toString()}`;
});

const items = useQuery({
  queryKey: ["items", apiPath],
  queryFn: ({ signal }) => request<ItemPage>(apiPath.value, { signal }),
  placeholderData: keepPreviousData,
});
const connections = useQuery({
  queryKey: ["connections"],
  queryFn: ({ signal }) => request<{ items: Connection[] }>("/connections", { signal }),
});

const search = ref(queryValue("q"));
let searchTimer: ReturnType<typeof setTimeout> | undefined;
watch(search, (value) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    if (value.trim() !== queryValue("q")) updateQuery({ q: value.trim() });
  }, 300);
});
watch(() => queryValue("q"), (value) => {
  if (value !== search.value.trim()) search.value = value;
});
onBeforeUnmount(() => clearTimeout(searchTimer));

function applySearch(value: string): void {
  clearTimeout(searchTimer);
  if (value !== queryValue("q")) updateQuery({ q: value });
}

function changeSort(field: ItemSortField): void {
  const first = field === "name" ? "asc" : "desc";
  const second = first === "asc" ? "desc" : "asc";
  const next = sort.value === `${field}_${first}` ? `${field}_${second}` : `${field}_${first}`;
  updateQuery({ sort: next === DEFAULT_SORT ? "" : next });
}

function resetFilters(): void {
  clearTimeout(searchTimer);
  search.value = "";
  updateQuery(Object.fromEntries(FILTER_KEYS.map((key) => [key, ""])));
}

function changePage(value: number): void {
  updateQuery({ offset: value > 0 ? String(value) : "" });
}
</script>

<template>
  <div class="view-content">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("items") }}</h1>
      <p>{{ t("itemsIntro") }}</p>
    </header>
    <div class="item-filters" role="search">
      <ReceiptSearch
        v-model="search"
        class="filter-search"
        kind="item"
        :placeholder="t('searchItems')"
        @select="applySearch"
      />
      <label>
        <span>{{ t("dateFrom") }}</span>
        <input type="date" :value="queryValue('from')" :max="queryValue('to') || undefined" @change="updateQuery({ from: inputValue($event) })">
      </label>
      <label>
        <span>{{ t("dateTo") }}</span>
        <input type="date" :value="queryValue('to')" :min="queryValue('from') || undefined" @change="updateQuery({ to: inputValue($event) })">
      </label>
      <label v-if="connections.data.value?.items.length">
        <span>{{ t("account") }}</span>
        <AppSelect class="account-select" :model-value="queryValue('connection')" :options="accountOptions(connections.data.value.items, t('allAccounts'))" :label="t('account')" @update:model-value="updateQuery({ connection: $event })" />
      </label>
      <label class="filter-amount">
        <span>{{ t("priceMin") }}</span>
        <input type="number" inputmode="decimal" min="0" step="0.01" :value="queryValue('min')" @change="updateQuery({ min: inputValue($event) })">
      </label>
      <label class="filter-amount">
        <span>{{ t("priceMax") }}</span>
        <input type="number" inputmode="decimal" min="0" step="0.01" :value="queryValue('max')" @change="updateQuery({ max: inputValue($event) })">
      </label>
      <button v-if="filtered" type="button" class="button secondary reset-filters" @click="resetFilters">
        {{ t("resetFilters") }}
      </button>
    </div>
    <QueryState
      :pending="items.isPending.value"
      :failed="items.isError.value"
      @retry="items.refetch()"
    >
      <section v-if="items.data.value" class="ledger-section" :class="{ updating: items.isPlaceholderData.value }">
        <div class="section-heading">
          <h2>{{ t("itemCount", items.data.value.total) }}</h2>
        </div>
        <ItemLedger v-if="items.data.value.items.length" :items="items.data.value.items" :sort="sort" @sort="changeSort" />
        <div v-else class="empty-state">
          <h3>{{ filtered ? t("noMatchingItems") : t("noReceipts") }}</h3>
        </div>
        <div v-if="items.data.value.total > limit" class="pagination">
          <span>{{
            t("pageRange", {
              start: offset + 1,
              end: Math.min(offset + limit, items.data.value.total),
              total: items.data.value.total,
            })
          }}</span>
          <div>
            <button class="button secondary" :disabled="offset === 0" @click="changePage(Math.max(0, offset - limit))">
              {{ t("previous") }}
            </button>
            <button class="button secondary" :disabled="offset + limit >= items.data.value.total" @click="changePage(offset + limit)">
              {{ t("next") }}
            </button>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>

<style scoped>
.item-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-bottom: 36px; }
.item-filters label { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--muted); }
.item-filters .filter-search { flex: 1 1 260px; }
.item-filters .filter-amount { width: 120px; }
.item-filters input,
.item-filters select { height: 40px; border: 1px solid var(--border-input); border-radius: 4px; background: var(--surface-input); padding: 0 12px; font-size: 13px; }
.item-filters input:focus,
.item-filters select:focus { border-color: var(--border-input-focus); box-shadow: 0 0 0 3px var(--ring-input-focus); outline: 0; }
.item-filters .account-select { min-width: 210px; }
.reset-filters { height: 40px; }
.ledger-section { transition: opacity 0.2s; }
.ledger-section.updating { opacity: 0.6; }
@media (max-width: 780px) {
  .item-filters { display: grid; grid-template-columns: 1fr 1fr; }
  .item-filters .filter-search,
  .reset-filters { grid-column: 1 / -1; }
  .item-filters .filter-amount { width: auto; }
  .item-filters input,
  .item-filters select, .item-filters .account-select { width: 100%; min-width: 0; }
}
</style>
