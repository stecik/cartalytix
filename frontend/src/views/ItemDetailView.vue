<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed, defineAsyncComponent, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { sourceName } from "../names";
import { useRoute } from "vue-router";
import { ApiError, request } from "../api";
import type { ItemDetail } from "../api";
import AppIcon from "../components/AppIcon.vue";
import QueryState from "../components/QueryState.vue";
import ExclusionAction from "../components/ExclusionAction.vue";
import ExclusionStatus from "../components/ExclusionStatus.vue";
import { formatMoney, numberLocale } from "../stats";

const PriceHistoryChart = defineAsyncComponent(
  () => import("../components/PriceHistoryChart.vue"),
);
const PURCHASE_LIMIT = 200;
const PAGE_SIZE = 20;

const { t, locale } = useI18n();
const route = useRoute();
const name = computed(() => (typeof route.query.name === "string" ? route.query.name : ""));
const currency = computed(() =>
  typeof route.query.currency === "string" ? route.query.currency : "",
);

const detail = useQuery({
  queryKey: ["item-detail", name, currency],
  queryFn: ({ signal }) =>
    request<ItemDetail>(
      `/items/detail?${new URLSearchParams({ name: name.value, currency: currency.value })}`,
      { signal },
    ),
  enabled: computed(() => name.value !== "" && currency.value !== ""),
  retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
});
// The chart uses every loaded purchase; only the table is paged.
const offset = ref(0);
const purchases = computed(() => detail.data.value?.purchases ?? []);
const includedPurchases = computed(() => purchases.value.filter((purchase) => !purchase.exclusion_reason));
const visiblePurchases = computed(() => purchases.value.slice(offset.value, offset.value + PAGE_SIZE));
watch([name, currency], () => { offset.value = 0; });
const missing = computed(
  () =>
    name.value === "" ||
    currency.value === "" ||
    (detail.error.value instanceof ApiError && detail.error.value.status === 404),
);

function money(amount: string): string {
  return formatMoney(amount, currency.value, locale.value);
}

function formatQuantity(quantity: string, unit: string | null): string {
  const value = new Intl.NumberFormat(numberLocale(locale.value), {
    maximumFractionDigits: 3,
  }).format(Number(quantity));
  return `${value} ${unit === "kg" ? "kg" : t("unitPiece")}`;
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
  <div class="view-content">
    <RouterLink class="text-link back-link" to="/items">
      <AppIcon name="arrow" />{{ t("backToItems") }}
    </RouterLink>
    <div v-if="missing" class="empty-state">
      <h3>{{ t("itemNotFound") }}</h3>
    </div>
    <QueryState
      v-else
      :pending="detail.isPending.value"
      :failed="detail.isError.value"
      @retry="detail.refetch()"
    >
      <template v-if="detail.data.value">
        <header class="page-heading">
          <div class="eyebrow">{{ t("items") }}</div>
          <h1>{{ sourceName(detail.data.value.name, t) }}</h1>
          <p>{{ formatQuantity(detail.data.value.quantity, detail.data.value.unit) }} · {{ t("receiptCount", detail.data.value.receipt_count) }}</p>
        </header>
        <div class="product-exclusion">
          <ExclusionAction scope="product" path="/items/exclusion" :identity="{ name, currency }" :excluded="detail.data.value.is_excluded" />
          <p>{{ t("productExclusionHint") }}</p>
          <ExclusionStatus v-if="detail.data.value.is_excluded" reason="product" />
        </div>
        <dl class="item-kpis">
          <div>
            <dt>{{ t("originalAmount") }}</dt>
            <dd>{{ money(detail.data.value.total_amount) }}</dd>
          </div>
          <div>
            <dt>{{ t("averagePrice") }}</dt>
            <dd>{{ money(detail.data.value.average_price) }}</dd>
          </div>
          <div>
            <dt>{{ t("lowestPrice") }}</dt>
            <dd class="low">{{ money(detail.data.value.min_price) }}</dd>
          </div>
          <div>
            <dt>{{ t("highestPrice") }}</dt>
            <dd class="high">{{ money(detail.data.value.max_price) }}</dd>
          </div>
        </dl>
        <div class="included-total"><span>{{ t("includedAmount") }}</span><strong>{{ money(detail.data.value.included_amount ?? detail.data.value.total_amount) }}</strong></div>
        <section v-if="includedPurchases.length > 1" class="detail-panel">
          <div class="section-heading">
            <h2>{{ t("priceHistory") }}</h2>
          </div>
          <PriceHistoryChart :purchases="includedPurchases" :currency="currency" />
        </section>
        <section class="detail-panel">
          <div class="section-heading">
            <h2>{{ t("purchaseHistory") }}</h2>
            <span v-if="detail.data.value.receipt_count > PURCHASE_LIMIT" class="section-note">
              {{ t("latestPurchasesNote", { count: PURCHASE_LIMIT }) }}
            </span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">{{ t("date") }}</th>
                  <th scope="col">{{ t("store") }}</th>
                  <th scope="col" class="amount">{{ t("quantity") }}</th>
                  <th scope="col" class="amount">{{ t("unitPrice") }}</th>
                  <th scope="col" class="amount">{{ t("amount") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(purchase, index) in visiblePurchases" :key="`${purchase.receipt_id}:${offset + index}`" :class="{ 'excluded-row': purchase.exclusion_reason }">
                  <td>
                    <RouterLink class="receipt-link" :to="{ name: 'receipt-detail', params: { id: purchase.receipt_id } }">
                      {{ formatDate(purchase.purchased_at) }}
                    </RouterLink>
                  </td>
                  <td>{{ purchase.merchant }}<ExclusionStatus :reason="purchase.exclusion_reason" /></td>
                  <td class="amount">{{ formatQuantity(purchase.quantity, purchase.unit) }}</td>
                  <td class="amount">{{ money(purchase.unit_price) }}</td>
                  <td class="amount strong">{{ money(purchase.total_amount) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="purchases.length > PAGE_SIZE" class="pagination">
            <span>{{ t("pageRange", { start: offset + 1, end: Math.min(offset + PAGE_SIZE, purchases.length), total: purchases.length }) }}</span>
            <div>
              <button type="button" class="button secondary" :disabled="offset === 0" @click="offset = Math.max(0, offset - PAGE_SIZE)">{{ t("previous") }}</button>
              <button type="button" class="button secondary" :disabled="offset + PAGE_SIZE >= purchases.length" @click="offset += PAGE_SIZE">{{ t("next") }}</button>
            </div>
          </div>
        </section>
      </template>
    </QueryState>
  </div>
</template>

<style scoped>
.back-link {
  margin-bottom: 18px;
}
.product-exclusion { margin-bottom: 22px; }
.product-exclusion p { margin: 8px 0; max-width: 720px; color: var(--muted); font-size: 12px; line-height: 1.7; }
.included-total { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 20px; font-size: 12px; color: var(--muted); padding-bottom: 24px; }
.included-total strong { font-size: 22px; color: var(--green); font-variant-numeric: tabular-nums; }
.excluded-row td { color: var(--muted); }
.item-kpis {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin: 0 0 8px;
  padding: 24px 0 30px;
  border-top: 1px solid var(--line);
}
.item-kpis > div {
  padding: 2px 24px;
  border-right: 1px solid var(--line);
}
.item-kpis > div:first-child {
  padding-left: 0;
}
.item-kpis > div:last-child {
  border-right: 0;
}
.item-kpis dt {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 12px;
}
.item-kpis dd {
  margin: 0;
  font-family: "Manrope Variable", sans-serif;
  font-size: 26px;
  font-weight: 500;
  letter-spacing: -1px;
  font-variant-numeric: tabular-nums;
}
.item-kpis dd.low {
  color: var(--text-positive);
}
.item-kpis dd.high {
  color: var(--text-negative);
}
.detail-panel {
  padding-top: 24px;
  border-top: 1px solid var(--line);
  margin-bottom: 32px;
}
.receipt-link {
  color: var(--green);
  font-weight: 550;
}
.receipt-link:hover {
  text-decoration: underline;
}
.strong {
  font-weight: 600;
  color: var(--green);
}
@media (max-width: 780px) {
  .item-kpis {
    grid-template-columns: 1fr 1fr;
    row-gap: 20px;
  }
  .item-kpis > div:nth-child(2) {
    border-right: 0;
  }
  .item-kpis > div:nth-child(3) {
    padding-left: 0;
  }
  .item-kpis dd {
    font-size: 22px;
  }
}
</style>
