<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import { accountOptions } from "../names";
import AppDateInput from "../components/AppDateInput.vue";
import AppSelect from "../components/AppSelect.vue";
import ExclusionFilter from "../components/ExclusionFilter.vue";
import FilterBar from "../components/FilterBar.vue";
import FilterField from "../components/FilterField.vue";
import RangeInput from "../components/RangeInput.vue";
import { itemKey, request } from "../api";
import type { ChangeOperation, Connection, ExclusionMode, ExclusionReason, ExclusionScope, ItemDetail, ItemExclusion, ItemExclusionPage, ItemPage, ItemSummary, ReceiptDetail } from "../api";
import ChangePreviewDialog from "../components/ChangePreviewDialog.vue";
import ItemLedger from "../components/ItemLedger.vue";
import SelectionBar from "../components/SelectionBar.vue";
import ExcludedItemsLedger from "../components/ExcludedItemsLedger.vue";
import ExclusionStatus from "../components/ExclusionStatus.vue";
import type { ItemSortField } from "../components/ItemLedger.vue";
import QueryState from "../components/QueryState.vue";
import ReceiptSearch from "../components/ReceiptSearch.vue";
import AppButton from "../components/AppButton.vue";
import PageHeader from "../components/PageHeader.vue";

type QueryKey = "q" | "from" | "to" | "connection" | "min" | "max" | "sort" | "offset" | "exclusion" | "exclusion_scope";

const DEFAULT_SORT = "receipt_count_desc";
const FILTER_KEYS = ["q", "from", "to", "connection", "min", "max", "exclusion", "exclusion_scope"] as const;
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
const exclusion = computed<ExclusionMode>(() => {
  const value = queryValue("exclusion");
  return value === "excluded" || value === "all" ? value : "included";
});
const exclusionScope = computed<ExclusionScope>(() => {
  const value = queryValue("exclusion_scope");
  return value === "product" || value === "item" ? value : "all";
});
const defaultSort = computed(() => exclusion.value === "excluded" ? "last_purchased_desc" : DEFAULT_SORT);
const sort = computed(() => {
  const value = queryValue("sort");
  const excludedSorts = ["name_asc", "name_desc", "last_purchased_asc", "last_purchased_desc", "total_amount_asc", "total_amount_desc"];
  return exclusion.value === "excluded" && !excludedSorts.includes(value) ? defaultSort.value : value || defaultSort.value;
});
const scopeOptions = computed(() => [
  { value: "all", label: t("allExclusionTypes") },
  { value: "product", label: t("globalExclusions") },
  { value: "item", label: t("oneOffExclusions") },
]);
const restoredState = ref<{ kind: ItemExclusion["kind"]; reasons: NonNullable<ExclusionReason>[] } | null>(null);
watch([exclusion, exclusionScope], () => { restoredState.value = null; });
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
    exclusion: exclusion.value,
  };
  for (const [key, value] of Object.entries(values)) if (value) params.set(key, value);
  return `/items?${params.toString()}`;
});

const items = useQuery({
  queryKey: ["items", apiPath],
  queryFn: ({ signal }) => request<ItemPage>(apiPath.value, { signal }),
  enabled: computed(() => exclusion.value !== "excluded"),
  placeholderData: keepPreviousData,
});
const exclusionsPath = computed(() => {
  const params = new URLSearchParams(apiPath.value.split("?")[1]);
  params.delete("exclusion");
  params.set("scope", exclusionScope.value);
  return "/items/exclusions?" + params.toString();
});
const itemExclusions = useQuery({
  queryKey: ["item-exclusions", exclusionsPath],
  queryFn: ({ signal }) => request<ItemExclusionPage>(exclusionsPath.value, { signal }),
  enabled: computed(() => exclusion.value === "excluded"),
  placeholderData: keepPreviousData,
});
const activeQuery = computed(() => exclusion.value === "excluded" ? itemExclusions : items);
const currentPage = computed(() => activeQuery.value.data.value);
watch([() => currentPage.value?.total, () => activeQuery.value.isPlaceholderData.value], ([total, placeholder]) => {
  if (!placeholder && total !== undefined && offset.value > 0 && offset.value >= total) {
    updateQuery({ offset: String(Math.floor(Math.max(0, total - 1) / limit) * limit || "") });
  }
});
// Selection covers only the visible page, so any page, filter or sort change starts over.
const selected = ref<ReadonlySet<string>>(new Set());
watch(apiPath, () => { selected.value = new Set(); });
const selectedItems = computed(() => (items.data.value?.items ?? []).filter((item) => selected.value.has(itemKey(item))));
const bulkChange = ref<{ title: string; operations: ChangeOperation[] } | null>(null);

function toggle(item: ItemSummary): void {
  const next = new Set(selected.value);
  const key = itemKey(item);
  if (!next.delete(key)) next.add(key);
  selected.value = next;
}

function bulkExclude(excluded: boolean): void {
  bulkChange.value = {
    title: t(excluded ? "excludeProduct" : "restoreProduct"),
    operations: selectedItems.value.map((item) => ({ kind: "product_exclusion", name: item.name, currency: item.currency, excluded })),
  };
}

function clearSelection(): void {
  selected.value = new Set();
}

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
  updateQuery({ sort: next === defaultSort.value ? "" : next });
}

function resetFilters(): void {
  clearTimeout(searchTimer);
  search.value = "";
  updateQuery(Object.fromEntries(FILTER_KEYS.map((key) => [key, ""])));
}

function changePage(value: number): void {
  updateQuery({ offset: value > 0 ? String(value) : "" });
}

function changeExclusion(value: string): void {
  updateQuery({ exclusion: value === "included" ? "" : value, exclusion_scope: "", sort: "" });
}

function restored(row: ItemExclusion, result: ReceiptDetail | ItemDetail | undefined): void {
  let reasons = row.blocked_by;
  if (row.kind === "item" && result && "items" in result) {
    const item = result.items.find((item) => item.id === row.item_id);
    reasons = item?.exclusion_reason ? [item.exclusion_reason] : [];
  }
  restoredState.value = { kind: row.kind, reasons };
}
</script>

<template>
  <div class="view-content">
    <PageHeader :eyebrow="t('workspace')" :title="t('items')" :intro="t('itemsIntro')" />
    <div class="item-display-controls">
      <ExclusionFilter :model-value="exclusion" @update:model-value="changeExclusion" />
      <AppSelect v-if="exclusion === 'excluded'" class="exclusion-scope" :model-value="exclusionScope" :options="scopeOptions" :label="t('exclusionScope')" @update:model-value="updateQuery({ exclusion_scope: $event === 'all' ? '' : $event })" />
    </div>
    <p v-if="restoredState" role="status" class="restoration-status">
      {{ restoredState.kind === "product" ? t("globalExclusionRemoved") : t("occurrenceExclusionRemoved") }}
      <template v-if="restoredState.reasons.length">
        <span>{{ t("otherExclusionsRemain") }}</span>
        <ExclusionStatus v-for="reason in restoredState.reasons" :key="reason" :reason="reason" />
      </template>
    </p>
    <FilterBar :filtered="filtered" @reset="resetFilters">
      <template #search>
        <ReceiptSearch
          v-model="search"
          kind="item"
          :exclusion="exclusion"
          :scope="exclusion === 'excluded' ? exclusionScope : undefined"
          :placeholder="t('searchItems')"
          @select="applySearch"
        />
      </template>
      <FilterField :label="t('dateFrom')">
        <AppDateInput :model-value="queryValue('from')" :max="queryValue('to')" @update:model-value="updateQuery({ from: $event })" />
      </FilterField>
      <FilterField :label="t('dateTo')">
        <AppDateInput :model-value="queryValue('to')" :min="queryValue('from')" @update:model-value="updateQuery({ to: $event })" />
      </FilterField>
      <FilterField v-if="connections.data.value?.items.length" :label="t('account')">
        <AppSelect :model-value="queryValue('connection')" :options="accountOptions(connections.data.value.items, t('allAccounts'))" :label="t('account')" @update:model-value="updateQuery({ connection: $event })" />
      </FilterField>
      <RangeInput :label="t('priceRange')" :min="queryValue('min')" :max="queryValue('max')" :min-label="t('priceMin')" :max-label="t('priceMax')" @update:min="updateQuery({ min: $event })" @update:max="updateQuery({ max: $event })" />
    </FilterBar>
    <QueryState
      :pending="activeQuery.isPending.value"
      :failed="activeQuery.isError.value"
      @retry="activeQuery.refetch()"
    >
      <section v-if="currentPage" class="ledger-section" :class="{ updating: activeQuery.isPlaceholderData.value }">
        <div class="section-heading">
          <h2>{{ t(exclusion === "excluded" ? "exclusionCount" : "itemCount", currentPage.total) }}</h2>
        </div>
        <ExcludedItemsLedger v-if="exclusion === 'excluded' && itemExclusions.data.value?.items.length" :rows="itemExclusions.data.value.items" :sort="sort" @sort="changeSort" @restored="restored" />
        <template v-else-if="exclusion !== 'excluded' && items.data.value?.items.length">
          <SelectionBar v-if="selectedItems.length" :summary="t('selectedProducts', selectedItems.length)" @clear="clearSelection">
            <AppButton v-if="selectedItems.some((item) => !item.is_excluded)" type="button" size="sm" data-action="exclude-selected" @click="bulkExclude(true)">{{ t("excludeProduct") }}</AppButton>
            <AppButton v-if="selectedItems.some((item) => item.is_excluded)" type="button" size="sm" data-action="restore-selected" @click="bulkExclude(false)">{{ t("restoreProduct") }}</AppButton>
          </SelectionBar>
          <ItemLedger :items="items.data.value.items" :sort="sort" :selected="selected" manage-exclusions @sort="changeSort" @toggle="toggle" />
        </template>
        <div v-else class="empty-state">
          <h3>{{ exclusion === "excluded" ? t("noMatchingExclusions") : filtered ? t("noMatchingItems") : t("noReceipts") }}</h3>
        </div>
        <p v-if="exclusion === 'excluded'" class="exclusion-note">
          {{ t("exclusionRegistryHint") }}
          <RouterLink class="text-link" to="/receipts?exclusion=excluded">{{ t("excludedReceipts") }}</RouterLink>
        </p>
        <div v-if="currentPage.total > limit" class="pagination">
          <span>{{
            t("pageRange", {
              start: offset + 1,
              end: Math.min(offset + limit, currentPage.total),
              total: currentPage.total,
            })
          }}</span>
          <div>
            <AppButton :disabled="offset === 0" @click="changePage(Math.max(0, offset - limit))">
              {{ t("previous") }}
            </AppButton>
            <AppButton :disabled="offset + limit >= currentPage.total" @click="changePage(offset + limit)">
              {{ t("next") }}
            </AppButton>
          </div>
        </div>
      </section>
    </QueryState>
    <ChangePreviewDialog v-if="bulkChange" :title="bulkChange.title" :body="{ operations: bulkChange.operations }" @applied="clearSelection" @close="bulkChange = null" />
  </div>
</template>

<style scoped>
.item-display-controls { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 24px; margin-bottom: 24px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.exclusion-scope { width: 176px; }
.exclusion-scope :deep(.app-select-trigger) { background: transparent; }
.restoration-status { margin-bottom: 20px; padding: 12px 16px; border: 1px solid var(--border); border-radius: 5px; color: var(--accent); background: var(--bg-muted); font-size: 13px; line-height: 1.7; }
.restoration-status > span { margin-left: 8px; }
.exclusion-note { margin-top: 18px; color: var(--text-muted); font-size: 12px; line-height: 1.8; }
.exclusion-note a { margin-left: 5px; }
.ledger-section { transition: opacity 0.2s; }
.ledger-section.updating { opacity: 0.6; }
</style>
