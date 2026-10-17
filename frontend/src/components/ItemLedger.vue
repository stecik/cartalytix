<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { sourceName } from "../names";
import { useRouter } from "vue-router";
import type { ItemSummary } from "../api";
import { formatMoney, numberLocale } from "../stats";

export type ItemSortField =
  | "name"
  | "receipt_count"
  | "total_amount"
  | "average_price"
  | "last_purchased";

const props = defineProps<{ items: ItemSummary[]; sort: string }>();
const emit = defineEmits<{ sort: [field: ItemSortField] }>();
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
    || (event.target instanceof Element && event.target.closest("a, button"))) return;
  void router.push(detailRoute(item));
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
    <table class="item-table">
      <thead>
        <tr>
          <th
            v-for="column in COLUMNS"
            :key="column.field"
            scope="col"
            :class="{ amount: column.numeric }"
            :aria-sort="ariaSort(column.field)"
          >
            <button type="button" class="sort-button" @click="emit('sort', column.field)">
              {{ t(column.label) }}<span aria-hidden="true">{{ SORT_MARKS[ariaSort(column.field)] }}</span>
            </button>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in items"
          :key="`${item.name}:${item.currency}`"
          class="item-row"
          @click="openItem($event, item)"
        >
          <td class="item-name">
            <RouterLink :to="detailRoute(item)">{{ sourceName(item.name, t) }}</RouterLink>
            <span class="row-detail">{{ formatQuantity(item) }}</span>
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
.item-row:hover {
  background: #eff1e8;
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
