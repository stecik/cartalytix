<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { Overview } from "../api";
import AppIcon from "../components/AppIcon.vue";
import QueryState from "../components/QueryState.vue";
import ReceiptLedger from "../components/ReceiptLedger.vue";

const { t, locale } = useI18n();
const overview = useQuery({
  queryKey: ["overview"],
  queryFn: ({ signal }) => request<Overview>("/overview", { signal }),
});

function formatAmount(amount: string, currency: string): string {
  return new Intl.NumberFormat(locale.value === "cs" ? "cs-CZ" : "en-GB", {
    style: "currency",
    currency,
  }).format(Number(amount));
}

function formatCount(count: number): string {
  return new Intl.NumberFormat(
    locale.value === "cs" ? "cs-CZ" : "en-GB",
  ).format(count);
}
</script>

<template>
  <div class="view-content">
    <header class="page-heading">
      <div class="eyebrow">{{ t("workspace") }}</div>
      <h1>{{ t("overview") }}</h1>
      <p>{{ t("overviewIntro") }}</p>
    </header>
    <QueryState
      :pending="overview.isPending.value"
      :failed="overview.isError.value"
      @retry="overview.refetch()"
    >
      <template v-if="overview.data.value">
        <dl class="stats-row">
          <div class="stat">
            <dt>{{ t("totalReceipts") }}</dt>
            <dd>{{ formatCount(overview.data.value.receipt_count) }}</dd>
          </div>
          <div class="stat">
            <dt>{{ t("connectedAccounts") }}</dt>
            <dd>{{ formatCount(overview.data.value.connection_count) }}</dd>
          </div>
          <div class="stat spending-stat">
            <dt>{{ t("spending") }}</dt>
            <dd
              v-if="overview.data.value.totals.length === 0"
              class="no-totals"
            >
              {{ t("noTotals") }}
            </dd>
            <dd
              v-for="total in overview.data.value.totals"
              :key="total.currency"
              class="currency-total"
            >
              {{ formatAmount(total.amount, total.currency) }}
            </dd>
            <span class="stat-note">{{ t("totalsNote") }}</span>
          </div>
        </dl>
        <section class="ledger-section">
          <div class="section-heading">
            <h2>{{ t("latestReceipts") }}</h2>
            <RouterLink class="text-link" to="/receipts">
{{ t("viewAll") }}<AppIcon name="arrow" />
</RouterLink>
          </div>
          <ReceiptLedger :receipts="overview.data.value.latest_receipts" />
        </section>
      </template>
    </QueryState>
  </div>
</template>
