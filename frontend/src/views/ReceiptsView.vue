<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { accountOptions } from "../names";
import AppDateInput from "../components/AppDateInput.vue";
import AppSelect from "../components/AppSelect.vue";
import ExclusionFilter from "../components/ExclusionFilter.vue";
import FilterBar from "../components/FilterBar.vue";
import FilterField from "../components/FilterField.vue";
import PageHeader from "../components/PageHeader.vue";
import RangeInput from "../components/RangeInput.vue";
import ManualReceiptDialog from "../components/ManualReceiptDialog.vue";
import { request } from "../api";
import type { Connection, ExclusionMode, ReceiptPage } from "../api";
import QueryState from "../components/QueryState.vue";
import ReceiptLedger from "../components/ReceiptLedger.vue";
import ReceiptSearch from "../components/ReceiptSearch.vue";
import AppButton from "../components/AppButton.vue";
import AppIcon from "../components/AppIcon.vue";

type QueryKey = "q" | "from" | "to" | "connection" | "min" | "max" | "sort" | "offset" | "exclusion";

const DEFAULT_SORT = "purchased_at_desc";
const FILTER_KEYS = ["q", "from", "to", "connection", "min", "max", "exclusion"] as const;
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const limit = 30;
const client = useQueryClient();
const adding = ref(false);

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

const offset = computed(() => {
  const value = Number(queryValue("offset"));
  return Number.isInteger(value) && value > 0 ? value : 0;
});
const sort = computed(() => queryValue("sort") || DEFAULT_SORT);
const exclusion = computed<ExclusionMode>(() => {
  const value = queryValue("exclusion");
  return value === "excluded" || value === "all" ? value : "included";
});
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
    exclusion: exclusion.value,
  };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return `/receipts?${params.toString()}`;
});

const receipts = useQuery({
  queryKey: ["receipts", apiPath],
  queryFn: ({ signal }) => request<ReceiptPage>(apiPath.value, { signal }),
  placeholderData: keepPreviousData,
});
watch([() => receipts.data.value?.total, receipts.isPlaceholderData], ([total, placeholder]) => {
  if (!placeholder && total !== undefined && offset.value > 0 && offset.value >= total) {
    updateQuery({ offset: String(Math.floor(Math.max(0, total - 1) / limit) * limit || "") });
  }
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

async function receiptsAdded(): Promise<void> {
  adding.value = false;
  await client.invalidateQueries();
}

function changePage(value: number): void {
  updateQuery({ offset: value > 0 ? String(value) : "" });
}
</script>

<template>
  <div class="view-content">
    <PageHeader :title="t('receipts')" :intro="t('receiptIntro')">
      <div class="receipts-actions">
        <AppButton type="button" variant="primary" @click="adding = true">{{ t("addReceipt") }}</AppButton>
        <RouterLink class="text-link" to="/changes">{{ t("changes") }}<AppIcon name="arrow" /></RouterLink>
      </div>
    </PageHeader>
    <ManualReceiptDialog v-if="adding" @close="adding = false" @saved="receiptsAdded" />
    <div class="exclusion-controls">
      <ExclusionFilter :model-value="exclusion" @update:model-value="updateQuery({ exclusion: $event === 'included' ? '' : $event })" />
    </div>
    <FilterBar :filtered="filtered" @reset="resetFilters">
      <template #search><ReceiptSearch v-model="search" :exclusion="exclusion" @select="applySearch" /></template>
      <FilterField :label="t('dateFrom')">
        <AppDateInput :model-value="queryValue('from')" :max="queryValue('to')" @update:model-value="updateQuery({ from: $event })" />
      </FilterField>
      <FilterField :label="t('dateTo')">
        <AppDateInput :model-value="queryValue('to')" :min="queryValue('from')" @update:model-value="updateQuery({ to: $event })" />
      </FilterField>
      <FilterField v-if="connections.data.value?.items.length" :label="t('account')">
        <AppSelect :model-value="queryValue('connection')" :options="accountOptions(connections.data.value.items, t('allAccounts'))" :label="t('account')" @update:model-value="updateQuery({ connection: $event })" />
      </FilterField>
      <RangeInput :label="t('amount')" :min="queryValue('min')" :max="queryValue('max')" :min-label="t('amountMin')" :max-label="t('amountMax')" @update:min="updateQuery({ min: $event })" @update:max="updateQuery({ max: $event })" />
    </FilterBar>
    <QueryState
      :pending="receipts.isPending.value"
      :failed="receipts.isError.value"
      @retry="receipts.refetch()"
    >
      <section v-if="receipts.data.value" class="ledger-section">
        <div class="section-heading">
          <h2>{{ t("receiptCount", receipts.data.value.total) }}</h2>
        </div>
        <ReceiptLedger :receipts="receipts.data.value.items" :sort="sort" :filtered="filtered" manage-exclusions @sort="changeSort" />
        <div v-if="receipts.data.value.total > limit" class="pagination">
          <span>{{
            t("pageRange", {
              start: offset + 1,
              end: Math.min(offset + limit, receipts.data.value.total),
              total: receipts.data.value.total,
            })
          }}</span>
          <div>
            <AppButton :disabled="offset === 0" @click="changePage(Math.max(0, offset - limit))">
              {{ t("previous") }}
            </AppButton>
            <AppButton :disabled="offset + limit >= receipts.data.value.total" @click="changePage(offset + limit)">
              {{ t("next") }}
            </AppButton>
          </div>
        </div>
      </section>
    </QueryState>
  </div>
</template>

<style scoped>
.exclusion-controls { margin-bottom: 24px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.receipts-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 24px; margin-top: 16px; }
</style>
