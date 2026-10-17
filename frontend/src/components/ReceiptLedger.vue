<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import type { Receipt } from "../api";
import AppIcon from "./AppIcon.vue";

defineProps<{ receipts: Receipt[] }>();
const { t, locale } = useI18n();
const router = useRouter();

function openReceipt(event: MouseEvent, id: string): void {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
    || (event.target instanceof Element && event.target.closest("a"))) return;
  void router.push({ name: "receipt-detail", params: { id } });
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
  <div v-if="receipts.length === 0" class="empty-state">
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
          <th scope="col">{{ t("date") }}</th>
          <th scope="col" class="amount">{{ t("amount") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="receipt in receipts" :key="receipt.id" class="receipt-row" @click="openReceipt($event, receipt.id)">
          <td>
            <RouterLink :to="{ name: 'receipt-detail', params: { id: receipt.id } }"><strong>{{ receipt.merchant }}</strong></RouterLink><span class="row-detail">{{ receipt.provider }}</span>
          </td>
          <td>{{ formatDate(receipt.purchased_at) }}</td>
          <td class="amount">
            {{ formatAmount(receipt.total_amount, receipt.currency) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.receipt-row { cursor: pointer; }
.receipt-row:hover { background: #e9ecdf70; }
.receipt-row a { display: inline-block; text-underline-offset: 4px; }
.receipt-row a:hover { text-decoration: underline; }
</style>
