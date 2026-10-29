<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import type { Receipt } from "../api";
import AppIcon from "./AppIcon.vue";
import ExclusionAction from "./ExclusionAction.vue";
import ExclusionStatus from "./ExclusionStatus.vue";
import ReceiptSource from "./ReceiptSource.vue";
import { providerLogos } from "../providerLogos";

const props = defineProps<{ receipts: Receipt[]; sort?: string; filtered?: boolean; manageExclusions?: boolean }>();
const emit = defineEmits<{ sort: [field: "purchased_at" | "total_amount"] }>();
const { t, locale } = useI18n();
const router = useRouter();
const SORT_MARKS = { ascending: "↑", descending: "↓", none: "" } as const;

function openReceipt(event: MouseEvent, id: string): void {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
    || (event.target instanceof Element && event.target.closest("a, button"))) return;
  void router.push({ name: "receipt-detail", params: { id } });
}

function ariaSort(field: string): "ascending" | "descending" | "none" {
  if (props.sort === `${field}_asc`) return "ascending";
  return props.sort === `${field}_desc` ? "descending" : "none";
}

function formatAmount(amount: string, currency: string): string {
  return new Intl.NumberFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", {
    style: "currency",
    currency,
  }).format(Number(amount));
}

function formatDate(date: string): string {
  const parsed = new Date(date);
  return Number.isNaN(parsed.getTime())
    ? t("unknownDate")
    : new Intl.DateTimeFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(parsed);
}
</script>

<template>
  <div v-if="receipts.length === 0 && filtered" class="empty-state">
    <h3>{{ t("noMatchingReceipts") }}</h3>
  </div>
  <div v-else-if="receipts.length === 0" class="empty-state">
    <div class="ledger-illustration" aria-hidden="true">
      <AppIcon name="receipts" />
    </div>
    <h3>{{ t("noReceipts") }}</h3>
    <p>{{ t("noReceiptsDescription") }}</p>
    <RouterLink class="text-link" to="/connections">
{{ t("exploreConnections") }}<AppIcon name="arrow" />
</RouterLink>
  </div>
  <div v-else class="table-wrap">
    <table>
      <thead>
        <tr>
          <th scope="col">{{ t("store") }}</th>
          <th v-if="sort === undefined" scope="col">{{ t("date") }}</th>
          <th v-else scope="col" :aria-sort="ariaSort('purchased_at')">
            <button type="button" class="sort-button" @click="emit('sort', 'purchased_at')">
              {{ t("date") }}<span aria-hidden="true">{{ SORT_MARKS[ariaSort("purchased_at")] }}</span>
            </button>
          </th>
          <th v-if="sort === undefined" scope="col" class="amount">{{ t("amount") }}</th>
          <th v-else scope="col" class="amount" :aria-sort="ariaSort('total_amount')">
            <button type="button" class="sort-button" @click="emit('sort', 'total_amount')">
              {{ t("amount") }}<span aria-hidden="true">{{ SORT_MARKS[ariaSort("total_amount")] }}</span>
            </button>
          </th>
          <th v-if="manageExclusions" scope="col" class="amount">{{ t("includedAmount") }}</th>
          <th v-if="manageExclusions" scope="col"><span class="sr-only">{{ t("actions") }}</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="receipt in receipts" :key="receipt.id" class="receipt-row" :class="{ 'excluded-row': receipt.is_excluded }" @click="openReceipt($event, receipt.id)">
          <td>
            <div class="receipt-store">
              <img v-if="providerLogos[receipt.provider]" :src="providerLogos[receipt.provider]" class="receipt-logo" width="28" height="28" alt="" />
              <div>
                <RouterLink :to="{ name: 'receipt-detail', params: { id: receipt.id } }"><strong>{{ receipt.merchant }}</strong></RouterLink><span class="row-detail"><ReceiptSource :provider="receipt.provider" :manual="receipt.is_manual" /></span>
                <ExclusionStatus v-if="receipt.is_excluded" reason="receipt" />
              </div>
            </div>
          </td>
          <td>{{ formatDate(receipt.purchased_at) }}</td>
          <td class="amount">
            {{ formatAmount(receipt.total_amount, receipt.currency) }}
          </td>
          <td v-if="manageExclusions" class="amount included-amount">{{ formatAmount(receipt.included_amount ?? receipt.total_amount, receipt.currency) }}</td>
          <td v-if="manageExclusions" class="actions-cell">
            <ExclusionAction scope="receipt" :path="`/receipts/${encodeURIComponent(receipt.id)}/exclusion`" :excluded="receipt.is_excluded" />
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.receipt-row { cursor: pointer; }
.excluded-row td { color: var(--muted); }
.actions-cell { width: 1%; }
.receipt-store { display: flex; align-items: center; gap: 12px; }
.receipt-logo { width: 28px; height: 28px; object-fit: contain; flex-shrink: 0; }
.sort-button { border: 0; padding: 0; background: none; color: inherit; font-size: inherit; display: inline-flex; gap: 4px; }
.sort-button:hover { color: var(--green); }
.sort-button span { min-width: 1ch; }
.receipt-row:hover { background: var(--surface-row-hover); }
.receipt-row a { display: inline-block; text-underline-offset: 4px; }
.receipt-row a:hover { text-decoration: underline; }
</style>
