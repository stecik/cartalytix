<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { connectionLabel } from "../names";
import { useRoute, useRouter } from "vue-router";
import { request } from "../api";
import type { Connection, ReceiptPage } from "../api";
import QueryState from "../components/QueryState.vue";
import ReceiptLedger from "../components/ReceiptLedger.vue";
import ReceiptSearch from "../components/ReceiptSearch.vue";

type QueryKey = "q" | "from" | "to" | "connection" | "min" | "max" | "sort" | "offset";

const DEFAULT_SORT = "purchased_at_desc";
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
    amount_min: queryValue("min"),
    amount_max: queryValue("max"),
    sort: sort.value,
  };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return `/receipts?${params.toString()}`;
});

const receipts = useQuery({
  queryKey: ["receipts", apiPath],
  queryFn: ({ signal }) => request<ReceiptPage>(apiPath.value, { signal }),
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

function changeSort(field: "purchased_at" | "total_amount"): void {
  const next = sort.value === `${field}_desc` ? `${field}_asc` : `${field}_desc`;
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
      <h1>{{ t("receipts") }}</h1>
      <p>{{ t("receiptIntro") }}</p>
    </header>
    <div class="receipt-filters" role="search">
      <ReceiptSearch v-model="search" class="filter-search" @select="applySearch" />
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
        <select :value="queryValue('connection')" @change="updateQuery({ connection: inputValue($event) })">
          <option value="">{{ t("allAccounts") }}</option>
          <option v-for="connection in connections.data.value.items" :key="connection.id" :value="connection.id">
            {{ connectionLabel(connection) }}
          </option>
        </select>
      </label>
      <label class="filter-amount">
        <span>{{ t("amountMin") }}</span>
        <input type="number" inputmode="decimal" min="0" step="0.01" :value="queryValue('min')" @change="updateQuery({ min: inputValue($event) })">
      </label>
      <label class="filter-amount">
        <span>{{ t("amountMax") }}</span>
        <input type="number" inputmode="decimal" min="0" step="0.01" :value="queryValue('max')" @change="updateQuery({ max: inputValue($event) })">
      </label>
      <button v-if="filtered" type="button" class="button secondary reset-filters" @click="resetFilters">
        {{ t("resetFilters") }}
      </button>
    </div>
    <QueryState
      :pending="receipts.isPending.value"
      :failed="receipts.isError.value"
      @retry="receipts.refetch()"
    >
      <section v-if="receipts.data.value" class="ledger-section">
        <div class="section-heading">
          <h2>{{ t("receiptCount", receipts.data.value.total) }}</h2>
        </div>
        <ReceiptLedger :receipts="receipts.data.value.items" :sort="sort" :filtered="filtered" @sort="changeSort" />
        <div v-if="receipts.data.value.total > limit" class="pagination">
          <span>{{
            t("pageRange", {
              start: offset + 1,
              end: Math.min(offset + limit, receipts.data.value.total),
              total: receipts.data.value.total,
            })
          }}</span>
          <div>
            <button
              class="button secondary"
              :disabled="offset === 0"
              @click="changePage(Math.max(0, offset - limit))"
            >
              {{ t("previous") }}
</button><button
              class="button secondary"
              :disabled="offset + limit >= receipts.data.value.total"
              @click="changePage(offset + limit)"
            >
              {{ t("next") }}
            </button>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>

<style scoped>
.receipt-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-bottom: 36px; }
.receipt-filters label { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--muted); }
.receipt-filters .filter-search { flex: 1 1 260px; }
.receipt-filters .filter-amount { width: 120px; }
.receipt-filters input,
.receipt-filters select { height: 40px; border: 1px solid #cbd1c3; border-radius: 4px; background: #fbfbf6; padding: 0 12px; font-size: 13px; }
.receipt-filters input:focus,
.receipt-filters select:focus { border-color: #67856c; box-shadow: 0 0 0 3px #dfe6d9; outline: 0; }
.reset-filters { height: 40px; }
@media (max-width: 780px) {
  .receipt-filters { display: grid; grid-template-columns: 1fr 1fr; }
  .receipt-filters .filter-search,
  .reset-filters { grid-column: 1 / -1; }
  .receipt-filters .filter-amount { width: auto; }
  .receipt-filters input,
  .receipt-filters select { width: 100%; min-width: 0; }
}
</style>
