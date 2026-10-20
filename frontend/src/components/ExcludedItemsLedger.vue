<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { ItemDetail, ItemExclusion, ReceiptDetail } from "../api";
import { formatMoney, numberLocale } from "../stats";
import { sourceName } from "../names";
import ExclusionAction from "./ExclusionAction.vue";
import ExclusionStatus from "./ExclusionStatus.vue";

type SortField = "name" | "last_purchased" | "total_amount";
const props = defineProps<{ rows: ItemExclusion[]; sort: string }>();
const emit = defineEmits<{
  sort: [field: SortField];
  restored: [row: ItemExclusion, result: ReceiptDetail | ItemDetail | undefined];
}>();
const { t, locale } = useI18n();
const columns: Array<{ field: SortField; label: string }> = [
  { field: "name", label: "product" },
  { field: "last_purchased", label: "date" },
  { field: "total_amount", label: "originalAmount" },
];
const sortMarks = { ascending: "↑", descending: "↓", none: "" } as const;

function ariaSort(field: SortField): "ascending" | "descending" | "none" {
  if (props.sort === field + "_asc") return "ascending";
  return props.sort === field + "_desc" ? "descending" : "none";
}

function date(value: string): string {
  return new Intl.DateTimeFormat(numberLocale(locale.value), {
    day: "numeric", month: "short", year: "numeric",
  }).format(new Date(value));
}
</script>

<template>
  <div class="table-wrap excluded-items">
    <table>
      <thead>
        <tr>
          <th v-for="column in columns" :key="column.field" scope="col" :aria-sort="ariaSort(column.field)" :class="{ amount: column.field === 'total_amount' }">
            <button class="sort-button" type="button" @click="emit('sort', column.field)">{{ t(column.label) }}<span aria-hidden="true">{{ sortMarks[ariaSort(column.field)] }}</span></button>
          </th>
          <th scope="col"><span class="sr-only">{{ t("actions") }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.kind === 'item' ? row.item_id : row.currency + ':' + row.name">
          <td class="exclusion-name">
            <RouterLink :to="{ name: 'item-detail', query: { name: row.name, currency: row.currency } }">{{ sourceName(row.name, t) }}</RouterLink>
            <span class="scope-label">{{ row.kind === "product" ? t("allPurchases") : t("thisReceiptOnly") }}</span>
            <template v-if="row.blocked_by.length">
              <span class="row-detail">{{ t("otherExclusionsRemain") }}</span>
              <ExclusionStatus v-for="reason in row.blocked_by" :key="reason" :reason="reason" />
            </template>
          </td>
          <td class="receipt-context">
            <template v-if="row.kind === 'item'">
              <RouterLink :to="{ name: 'receipt-detail', params: { id: row.receipt_id } }">{{ date(row.purchased_at) }}</RouterLink>
              <span class="row-detail">{{ row.merchant }}</span>
            </template>
            <template v-else>
              <span>{{ date(row.purchased_at) }}</span>
              <span class="row-detail">{{ t("lastPurchased") }}</span>
            </template>
          </td>
          <td class="amount">{{ formatMoney(row.total_amount, row.currency, locale) }}</td>
          <td class="restore-cell">
            <ExclusionAction v-if="row.kind === 'item'" scope="item" :path="'/receipts/' + encodeURIComponent(row.receipt_id) + '/items/' + encodeURIComponent(row.item_id) + '/exclusion'" :excluded="true" :restore-label="t('restoreExcludedOccurrence')" @saved="emit('restored', row, $event)" />
            <ExclusionAction v-else scope="product" path="/items/exclusion" :identity="{ name: row.name, currency: row.currency }" :excluded="true" @saved="emit('restored', row, $event)" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.sort-button { display: inline-flex; align-items: center; gap: 4px; border: 0; padding: 0; background: transparent; color: inherit; font: inherit; }
.sort-button:hover { color: var(--green); }
.sort-button span { min-width: 1ch; }
.exclusion-name { min-width: 180px; white-space: normal; }
.exclusion-name > a, .receipt-context > a { color: var(--green); text-underline-offset: 3px; }
.exclusion-name > a:hover, .receipt-context > a:hover { text-decoration: underline; }
.exclusion-name > a { font-weight: 550; }
.scope-label { display: block; width: fit-content; margin-top: 7px; border: 1px solid var(--line); border-radius: 4px; padding: 3px 7px; color: var(--muted); font-size: 11px; }
.restore-cell { width: 1%; min-width: 140px; }
.receipt-context { min-width: 130px; }
@media (max-width: 780px) {
  .excluded-items { overflow: visible; }
  table, tbody { display: block; }
  thead { display: block; }
  thead tr { display: flex; flex-wrap: wrap; gap: 16px; border-bottom: 1px solid var(--line); }
  thead th { padding: 12px 0; border: 0; }
  thead th:last-child { display: none; }
  tbody tr { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 20px 0; border-bottom: 1px solid var(--line); }
  tbody td { padding: 0; border: 0; min-width: 0; }
  .exclusion-name { grid-column: 1 / -1; overflow-wrap: anywhere; }
  .receipt-context { white-space: normal; }
  .restore-cell { grid-column: 1 / -1; width: auto; }
}
</style>
