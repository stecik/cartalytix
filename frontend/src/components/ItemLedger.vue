<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { sourceName } from "../names";
import { useRouter } from "vue-router";
import { itemKey } from "../api";
import type { ItemSummary } from "../api";
import { formatMoney, numberLocale } from "../stats";
import ExclusionAction from "./ExclusionAction.vue";
import ExclusionStatus from "./ExclusionStatus.vue";

export type ItemSortField =
  | "name"
  | "receipt_count"
  | "total_amount"
  | "average_price"
  | "last_purchased";

/** Without `sort` headers are static; with `selected` rows toggle a checkbox instead of opening. */
const props = defineProps<{ items: ItemSummary[]; sort?: string; selected?: ReadonlySet<string>; manageExclusions?: boolean }>();
const emit = defineEmits<{ sort: [field: ItemSortField]; toggle: [item: ItemSummary] }>();
const { t, locale } = useI18n();
const router = useRouter();
const SORT_MARKS = { ascending: "↑", descending: "↓", none: "" } as const;
const COLUMNS: Array<{ field: ItemSortField; label: string; numeric: boolean }> = [
  { field: "name", label: "product", numeric: false },
  { field: "receipt_count", label: "purchases", numeric: true },
  { field: "total_amount", label: "spent", numeric: true },
  { field: "average_price", label: "averagePrice", numeric: true },
  { field: "last_purchased", label: "lastPurchased", numeric: true },
];

function detailRoute(item: ItemSummary) {
  return { name: "item-detail", query: { name: item.name, currency: item.currency } };
}

function openItem(event: MouseEvent, item: ItemSummary): void {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
    || (event.target instanceof Element && event.target.closest("a, button, label"))) return;
  if (props.selected) emit("toggle", item);
  else void router.push(detailRoute(item));
}

function ariaSort(field: ItemSortField): "ascending" | "descending" | "none" {
  if (props.sort === `${field}_asc`) return "ascending";
  return props.sort === `${field}_desc` ? "descending" : "none";
}

function formatQuantity(item: ItemSummary): string {
  const value = new Intl.NumberFormat(numberLocale(locale.value), {
    maximumFractionDigits: 3,
  }).format(Number(item.quantity));
  return `${value} ${item.unit === "kg" ? "kg" : t("unitPiece")}`;
}

function formatDate(date: string): string {
  return new Intl.DateTimeFormat(numberLocale(locale.value), {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(date));
}
</script>

<template>
  <div class="table-wrap">
    <table class="item-table" :class="{ selectable: selected }">
      <thead>
        <tr>
          <th v-if="selected" scope="col" class="select-cell"><span class="sr-only">{{ t("select") }}</span></th>
          <th
            v-for="column in COLUMNS"
            :key="column.field"
            scope="col"
            :class="{ amount: column.numeric }"
            :aria-sort="sort === undefined ? undefined : ariaSort(column.field)"
          >
            <button v-if="sort !== undefined" type="button" class="sort-button" @click="emit('sort', column.field)">
              {{ t(column.field === 'total_amount' && (manageExclusions || selected) ? 'originalAmount' : column.label) }}<span aria-hidden="true">{{ SORT_MARKS[ariaSort(column.field)] }}</span>
            </button>
            <template v-else>{{ t(column.field === 'total_amount' && (manageExclusions || selected) ? 'originalAmount' : column.label) }}</template>
          </th>
          <th v-if="manageExclusions || selected" scope="col" class="amount">{{ t("includedAmount") }}</th>
          <th v-if="manageExclusions" scope="col"><span class="sr-only">{{ t("actions") }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in items"
          :key="itemKey(item)"
          class="item-row"
          :class="{ selected: selected?.has(itemKey(item)), 'excluded-row': item.is_excluded }"
          @click="openItem($event, item)"
        >
          <td v-if="selected" class="select-cell">
            <label>
              <input type="checkbox" :checked="selected.has(itemKey(item))" @change="emit('toggle', item)">
              <span class="sr-only">{{ t("selectItem", { name: sourceName(item.name, t) }) }}</span>
            </label>
          </td>
          <td class="item-name">
            <RouterLink :to="detailRoute(item)">{{ sourceName(item.name, t) }}</RouterLink>
            <span class="row-detail">{{ formatQuantity(item) }}</span>
            <ExclusionStatus v-if="item.is_excluded" reason="product" />
          </td>
          <td class="amount">{{ item.receipt_count }}</td>
          <td class="amount strong">{{ formatMoney(item.total_amount, item.currency, locale) }}</td>
          <td class="amount">
            {{ formatMoney(item.average_price, item.currency, locale) }}
            <span v-if="item.min_price !== item.max_price" class="row-detail">
              {{ formatMoney(item.min_price, item.currency, locale) }} – {{ formatMoney(item.max_price, item.currency, locale) }}
            </span>
          </td>
          <td class="amount">{{ formatDate(item.last_purchased_at) }}</td>
          <td v-if="manageExclusions || selected" class="amount included-amount">{{ formatMoney(item.included_amount ?? item.total_amount, item.currency, locale) }}</td>
          <td v-if="manageExclusions" class="actions-cell">
            <ExclusionAction scope="product" path="/items/exclusion" :identity="{ name: item.name, currency: item.currency }" :excluded="item.is_excluded" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.item-row {
  cursor: pointer;
  transition: background 0.12s;
}
.excluded-row td { color: var(--muted); }
.actions-cell { width: 1%; }
.item-row:hover,
.item-row.selected {
  background: var(--surface-item-ledger);
}
.select-cell {
  width: 1%;
  padding-right: 4px;
}
.selectable td:first-child,
.selectable th:first-child {
  padding-left: 14px;
}
.selectable td:last-child,
.selectable th:last-child {
  padding-right: 14px;
}
.select-cell label {
  display: flex;
  cursor: pointer;
}
.select-cell input {
  width: 17px;
  height: 17px;
  margin: 0;
  accent-color: var(--green);
}
.item-name {
  white-space: normal;
  min-width: 180px;
}
.item-name a {
  font-weight: 550;
  color: var(--green);
}
.item-name a:hover {
  text-decoration: underline;
}
.strong {
  font-weight: 600;
  color: var(--green);
}
.sort-button {
  border: 0;
  background: none;
  padding: 0;
  color: inherit;
  font: inherit;
  display: inline-flex;
  gap: 4px;
}
th.amount .sort-button {
  justify-content: flex-end;
}
.sort-button:hover {
  color: var(--green);
}
td.amount,
th.amount {
  padding-left: 18px;
}
</style>
