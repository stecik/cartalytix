<script setup lang="ts">
import { computed } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { ApiError, request } from "../api";
import type { ReceiptDetail, ReceiptDiscount } from "../api";
import QueryState from "../components/QueryState.vue";

const route = useRoute();
const { t, locale } = useI18n();
const receiptId = computed(() => String(route.params.id ?? ""));
const receipt = useQuery({
  queryKey: computed(() => ["receipts", "detail", receiptId.value]),
  queryFn: ({ signal }) => request<ReceiptDetail>(`/receipts/${encodeURIComponent(receiptId.value)}`, { signal }),
});
const notFound = computed(() => receipt.error.value instanceof ApiError && receipt.error.value.status === 404);
const language = computed(() => locale.value === "cs" ? "cs-CZ" : "en-GB");
const amountFormatter = computed(() => receipt.data.value ? new Intl.NumberFormat(language.value, {
  style: "currency", currency: receipt.data.value.currency, maximumFractionDigits: 2,
}) : null);
const unitPriceFormatter = computed(() => receipt.data.value ? new Intl.NumberFormat(language.value, {
  style: "currency", currency: receipt.data.value.currency, maximumFractionDigits: 4,
}) : null);
const quantityFormatter = computed(() => new Intl.NumberFormat(language.value, { maximumFractionDigits: 4 }));
const dateFormatter = computed(() => new Intl.DateTimeFormat(language.value, { dateStyle: "medium", timeStyle: "short" }));
const discounts = computed(() => {
  const itemIds = new Set(receipt.data.value?.items.map((item) => item.id));
  const grouped = new Map<string | null, ReceiptDiscount[]>();
  for (const discount of receipt.data.value?.discounts ?? []) {
    const key = discount.item_id && itemIds.has(discount.item_id) ? discount.item_id : null;
    const items = grouped.get(key) ?? [];
    items.push(discount);
    grouped.set(key, items);
  }
  return grouped;
});

function money(value: string, unitPrice = false): string {
  const formatter = unitPrice ? unitPriceFormatter.value : amountFormatter.value;
  return formatter?.format(Number(value)) ?? "";
}

function quantity(value: string, unit: string | null): string {
  const amount = quantityFormatter.value.format(Number(value));
  const label = unit === "piece" ? t("unitPiece") : unit;
  return label ? `${amount} ${label}` : amount;
}

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? t("unknownDate") : dateFormatter.value.format(parsed);
}
</script>

<template>
  <div class="view-content receipt-detail">
    <RouterLink to="/receipts" class="text-link receipt-back">{{ t("backToReceipts") }}</RouterLink>
    <header class="page-heading">
      <div class="eyebrow">{{ t("receiptDetails") }}</div>
      <h1>{{ receipt.data.value?.merchant ?? t("receiptDetails") }}</h1>
      <p v-if="receipt.data.value"><time :datetime="receipt.data.value.purchased_at">{{ date(receipt.data.value.purchased_at) }}</time><span class="receipt-provider">{{ receipt.data.value.provider }}</span></p>
    </header>
    <p v-if="notFound" role="alert" class="form-alert">{{ t("receiptNotFound") }}</p>
    <QueryState v-else :pending="receipt.isPending.value" :failed="receipt.isError.value" @retry="receipt.refetch()">
      <template v-if="receipt.data.value">
        <p v-if="receipt.data.value.quality_issues.length" role="status" class="receipt-quality">{{ t("receiptQualityWarning") }}</p>
        <section class="receipt-items" :aria-label="t('receiptItems')">
          <div class="section-heading"><h2>{{ t("receiptItems") }}</h2></div>
          <p v-if="!receipt.data.value.items.length" class="section-note">{{ t("noReceiptItems") }}</p>
          <ol v-else class="receipt-item-list">
            <li v-for="item in receipt.data.value.items" :key="item.id">
              <div class="receipt-item-heading"><h3>{{ item.name }}</h3><strong>{{ money(item.total_amount) }}</strong></div>
              <dl class="receipt-item-facts">
                <div><dt>{{ t("quantity") }}</dt><dd>{{ quantity(item.quantity, item.unit) }}</dd></div>
                <div v-if="item.unit_price !== null"><dt>{{ t("unitPrice") }}</dt><dd>{{ money(item.unit_price, true) }}</dd></div>
                <div v-if="item.original_amount !== null"><dt>{{ t("beforeCoupon") }}</dt><dd>{{ money(item.original_amount) }}</dd></div>
                <div v-if="item.deposit_amount !== null"><dt>{{ t("deposit") }}</dt><dd>{{ money(item.deposit_amount) }}</dd></div>
                <div v-if="item.printed_net_amount !== null"><dt>{{ t("printedNetAmount") }}</dt><dd>{{ money(item.printed_net_amount) }}</dd></div>
                <div v-if="item.source_code"><dt>{{ t("productCode") }}</dt><dd>{{ item.source_code }}</dd></div>
                <div v-if="item.warranty"><dt>{{ t("warranty") }}</dt><dd>{{ item.warranty }}</dd></div>
              </dl>
              <p v-if="item.is_weight" class="section-note">{{ t("weighedItem") }}</p>
              <ul v-if="discounts.get(item.id)?.length" class="receipt-discounts" :aria-label="t('itemDiscounts')">
                <li v-for="discount in discounts.get(item.id)" :key="discount.id"><span>{{ discount.name || t("discount") }}</span><span>{{ money(discount.amount) }}</span></li>
              </ul>
            </li>
          </ol>
        </section>
        <section v-if="discounts.get(null)?.length" class="receipt-level-discounts" :aria-label="t('receiptDiscounts')">
          <div class="section-heading"><h2>{{ t("receiptDiscounts") }}</h2></div>
          <ul class="receipt-discounts"><li v-for="discount in discounts.get(null)" :key="discount.id"><span>{{ discount.name || t("discount") }}</span><span>{{ money(discount.amount) }}</span></li></ul>
        </section>
        <p v-if="receipt.data.value.discounts.length" class="section-note">{{ t("discountValuesNote") }}</p>
        <dl class="receipt-total">
          <div v-if="Number(receipt.data.value.rounding_amount) !== 0" class="receipt-rounding"><dt>{{ t("rounding") }}</dt><dd>{{ money(receipt.data.value.rounding_amount) }}</dd></div>
          <div><dt>{{ t("receiptTotal") }}</dt><dd>{{ money(receipt.data.value.total_amount) }}</dd></div>
        </dl>
      </template>
    </QueryState>
  </div>
</template>

<style scoped>
.receipt-back { margin-bottom: 25px; }
.receipt-provider { margin-left: 18px; }
.receipt-items { border-top: 1px solid var(--line); padding-top: 24px; }
.receipt-item-list { list-style: none; padding: 0; margin: 20px 0 0; }
.receipt-item-list > li { padding: 24px 0; border-top: 1px solid var(--line); }
.receipt-item-heading { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; }
.receipt-item-heading h3 { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.receipt-item-heading > strong { white-space: nowrap; font-size: 15px; font-variant-numeric: tabular-nums; }
.receipt-item-facts { display: flex; flex-wrap: wrap; gap: 14px 32px; font-size: 12px; margin: 18px 0 0; }
.receipt-item-facts dt { color: var(--muted); margin-bottom: 6px; }
.receipt-item-facts dd { margin: 0; overflow-wrap: anywhere; }
.receipt-discounts { list-style: none; margin: 16px 0 0; padding: 0; font-size: 12px; }
.receipt-discounts li { display: flex; justify-content: space-between; gap: 20px; padding: 9px 0; color: var(--green); }
.receipt-discounts li > span:last-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-level-discounts { border-top: 1px solid var(--line); padding-top: 24px; margin-top: 12px; }
.receipt-total { border-top: 1px solid var(--line); padding-top: 24px; margin: 26px 0; }
.receipt-total > div { display: flex; justify-content: space-between; align-items: baseline; gap: 20px; font-size: 24px; font-weight: 600; }
.receipt-total dd { margin: 0; white-space: nowrap; font-variant-numeric: tabular-nums; }
.receipt-total > .receipt-rounding { font-size: 12px; font-weight: 400; margin-bottom: 20px; color: var(--muted); }
.receipt-quality { background: #f0ebdb; color: #77623d; font-size: 13px; padding: 18px; margin-bottom: 24px; line-height: 1.7; }
</style>
