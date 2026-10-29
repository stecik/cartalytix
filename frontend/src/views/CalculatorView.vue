<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { accountOptions, sourceName } from "../names";
import AppDateInput from "../components/AppDateInput.vue";
import AppSelect from "../components/AppSelect.vue";
import FilterBar from "../components/FilterBar.vue";
import FilterField from "../components/FilterField.vue";
import { itemKey, request } from "../api";
import type { Connection, ItemKey, ItemPage, ItemTotals } from "../api";
import ItemLedger from "../components/ItemLedger.vue";
import type { ItemSortField } from "../components/ItemLedger.vue";
import QueryState from "../components/QueryState.vue";
import ReceiptSearch from "../components/ReceiptSearch.vue";
import { formatMoney } from "../stats";
import AppButton from "../components/AppButton.vue";
import PageHeader from "../components/PageHeader.vue";

type QueryKey = "q" | "from" | "to" | "connection" | "sort" | "offset";

const DEFAULT_SORT = "receipt_count_desc";
const FILTER_KEYS = ["q", "from", "to", "connection"] as const;
const STORAGE_KEY = "cartalytix-calculator";
const MAX_SELECTION = 200;
const { t, locale } = useI18n();
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

function dayStart(value: string, days = 0): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return "";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function loadSelection(): ItemKey[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(saved)) return [];
    return saved
      .filter((item): item is ItemKey => typeof item?.name === "string" && typeof item?.currency === "string")
      .map(({ name, currency }) => ({ name, currency }))
      .slice(0, MAX_SELECTION);
  } catch {
    return [];
  }
}

const selection = ref<ItemKey[]>(loadSelection());
const selectedKeys = computed(() => new Set(selection.value.map(itemKey)));
watch(selection, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Storage may be unavailable (private mode); the selection then lives until reload.
  }
}, { deep: true });

function toggle(item: ItemKey): void {
  const key = itemKey(item);
  if (selectedKeys.value.has(key))
    selection.value = selection.value.filter((selected) => itemKey(selected) !== key);
  else if (selection.value.length < MAX_SELECTION)
    selection.value = [...selection.value, { name: item.name, currency: item.currency }];
}

function clearSelection(): void {
  selection.value = [];
}

const offset = computed(() => {
  const value = Number(queryValue("offset"));
  return Number.isInteger(value) && value > 0 ? value : 0;
});
const sort = computed(() => queryValue("sort") || DEFAULT_SORT);
const filtered = computed(() => FILTER_KEYS.some((key) => queryValue(key) !== ""));
const period = computed(() => ({
  purchased_from: dayStart(queryValue("from")),
  purchased_to: dayStart(queryValue("to"), 1),
  connection_id: queryValue("connection"),
}));
const apiPath = computed(() => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset.value) });
  const values = { q: queryValue("q").trim(), ...period.value, sort: sort.value };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return `/items?${params.toString()}`;
});
const totalsBody = computed(() => {
  const body: Record<string, unknown> = { items: selection.value };
  for (const [key, value] of Object.entries(period.value)) if (value) body[key] = value;
  return JSON.stringify(body);
});

const items = useQuery({
  queryKey: ["items", apiPath],
  queryFn: ({ signal }) => request<ItemPage>(apiPath.value, { signal }),
  placeholderData: keepPreviousData,
});
const totals = useQuery({
  queryKey: ["item-totals", totalsBody],
  queryFn: ({ signal }) =>
    request<ItemTotals>("/items/totals", { method: "POST", body: totalsBody.value, signal }),
  enabled: computed(() => selection.value.length > 0),
  placeholderData: keepPreviousData,
});
const connections = useQuery({
  queryKey: ["connections"],
  queryFn: ({ signal }) => request<{ items: Connection[] }>("/connections", { signal }),
});

const unpurchased = computed(() => {
  const bought = new Set(totals.data.value?.items.map(itemKey));
  return selection.value.filter((item) => !bought.has(itemKey(item)));
});
const currencyTotals = computed(() => {
  const values = totals.data.value?.totals ?? [];
  const first = selection.value[0];
  return values.length || !first ? values : [{ currency: first.currency, total_amount: "0" }];
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
    <PageHeader :eyebrow="t('workspace')" :title="t('calculator')" :intro="t('calculatorIntro')" />
    <FilterBar :filtered="filtered" @reset="resetFilters">
      <template #search><ReceiptSearch v-model="search" kind="item" :placeholder="t('searchItems')" @select="applySearch" /></template>
      <FilterField :label="t('dateFrom')">
        <AppDateInput :model-value="queryValue('from')" :max="queryValue('to')" @update:model-value="updateQuery({ from: $event })" />
      </FilterField>
      <FilterField :label="t('dateTo')">
        <AppDateInput :model-value="queryValue('to')" :min="queryValue('from')" @update:model-value="updateQuery({ to: $event })" />
      </FilterField>
      <FilterField v-if="connections.data.value?.items.length" :label="t('account')">
        <AppSelect :model-value="queryValue('connection')" :options="accountOptions(connections.data.value.items, t('allAccounts'))" :label="t('account')" @update:model-value="updateQuery({ connection: $event })" />
      </FilterField>
    </FilterBar>
    <section class="selection-section" :class="{ updating: totals.isPlaceholderData.value }">
      <div class="section-heading">
        <h2>{{ t("calculatorSelection") }}</h2>
        <AppButton v-if="selection.length" type="button" @click="clearSelection">
          {{ t("clearSelection") }}
        </AppButton>
      </div>
      <div v-if="!selection.length" class="empty-state">
        <h3>{{ t("calculatorEmpty") }}</h3>
      </div>
      <QueryState
        v-else
        :pending="totals.isPending.value"
        :failed="totals.isError.value"
        @retry="totals.refetch()"
      >
        <dl class="stats-row calculator-stats">
          <div class="stat">
            <dt>{{ t("calculatorTotal") }}</dt>
            <dd v-for="total in currencyTotals" :key="total.currency" :class="{ 'currency-total': currencyTotals.length > 1 }">
              {{ formatMoney(total.total_amount, total.currency, locale) }}
            </dd>
          </div>
          <div class="stat">
            <dt>{{ t("calculatorSelection") }}</dt>
            <dd>{{ selection.length }}</dd>
          </div>
        </dl>
        <ItemLedger v-if="totals.data.value?.items.length" :items="totals.data.value.items" :selected="selectedKeys" @toggle="toggle" />
        <div v-if="unpurchased.length" class="unpurchased">
          <span>{{ t("calculatorNoPurchases") }}:</span>
          <button
            v-for="item in unpurchased"
            :key="itemKey(item)"
            type="button"
            class="unpurchased-item"
            :aria-label="t('removeItem', { name: sourceName(item.name, t) })"
            @click="toggle(item)"
          >
            {{ sourceName(item.name, t) }} <span aria-hidden="true">×</span>
          </button>
        </div>
      </QueryState>
    </section>
    <QueryState
      :pending="items.isPending.value"
      :failed="items.isError.value"
      @retry="items.refetch()"
    >
      <section v-if="items.data.value" class="ledger-section" :class="{ updating: items.isPlaceholderData.value }">
        <div class="section-heading">
          <h2>{{ t("itemCount", items.data.value.total) }}</h2>
        </div>
        <ItemLedger v-if="items.data.value.items.length" :items="items.data.value.items" :sort="sort" :selected="selectedKeys" @sort="changeSort" @toggle="toggle" />
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
            <AppButton :disabled="offset === 0" @click="changePage(Math.max(0, offset - limit))">
              {{ t("previous") }}
            </AppButton>
            <AppButton :disabled="offset + limit >= items.data.value.total" @click="changePage(offset + limit)">
              {{ t("next") }}
            </AppButton>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>

<style scoped>
.selection-section { margin-bottom: 48px; }
.calculator-stats { grid-template-columns: 1.5fr 1fr; margin: 12px 0 24px; }
.unpurchased { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 16px; font-size: 12px; color: var(--text-muted); }
.unpurchased-item { border: 1px solid var(--border); border-radius: 4px; background: none; padding: 4px 8px; color: inherit; font: inherit; }
.unpurchased-item:hover { color: var(--accent); border-color: var(--accent); }
.selection-section,
.ledger-section { transition: opacity 0.2s; }
.selection-section.updating,
.ledger-section.updating { opacity: 0.6; }
</style>
