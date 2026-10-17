<script setup lang="ts">
import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { computed, defineAsyncComponent, ref } from "vue";
import { useI18n } from "vue-i18n";
import { request } from "../api";
import type { CurrencyStats, Overview, Stats, StatsPeriod } from "../api";
import AppIcon from "../components/AppIcon.vue";
import QueryState from "../components/QueryState.vue";
import RankedBars from "../components/RankedBars.vue";
import type { RankedRow } from "../components/RankedBars.vue";
import ReceiptLedger from "../components/ReceiptLedger.vue";
import {
  formatLongMonth,
  formatMoney,
  numberLocale,
  percentChange,
} from "../stats";

const MonthlySpendingChart = defineAsyncComponent(
  () => import("../components/MonthlySpendingChart.vue"),
);
const ShoppingHeatmap = defineAsyncComponent(
  () => import("../components/ShoppingHeatmap.vue"),
);

const PERIODS: StatsPeriod[] = ["12m", "year", "all"];

const { t, locale } = useI18n();
const period = ref<StatsPeriod>("12m");
const month = ref<string | null>(null);

const overview = useQuery({
  queryKey: ["overview"],
  queryFn: ({ signal }) => request<Overview>("/overview", { signal }),
});
const stats = useQuery({
  queryKey: ["overview-stats", period, month],
  queryFn: ({ signal }) => {
    const params = new URLSearchParams({ period: period.value });
    if (month.value) params.set("month", month.value);
    return request<Stats>(`/overview/stats?${params}`, { signal });
  },
  placeholderData: keepPreviousData,
});

const primary = computed<CurrencyStats | null>(() => {
  const data = stats.data.value;
  return data?.totals.find((total) => total.currency === data.currency) ?? null;
});
const otherTotals = computed(() =>
  (stats.data.value?.totals ?? [])
    .filter((total) => total.currency !== stats.data.value?.currency)
    .map((total) => formatMoney(total.amount, total.currency, locale.value))
    .join(", "),
);
const comparisonLabel = computed(() =>
  month.value ? t("vsPreviousMonth") : t("vsPreviousPeriod"),
);

interface Trend {
  text: string;
  direction: "up" | "down" | "flat";
}

function trend(current: number, previous: number | null): Trend | null {
  const change = percentChange(current, previous);
  if (change === null) return null;
  const text = new Intl.NumberFormat(numberLocale(locale.value), {
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  }).format(change);
  const direction = Math.abs(change) < 0.05 ? "flat" : change > 0 ? "up" : "down";
  return { text: `${text} %`, direction };
}

const spendTrend = computed(() => {
  const total = primary.value;
  if (!total || total.previous_amount === null) return null;
  return trend(Number(total.amount), Number(total.previous_amount));
});
const countTrend = computed(() => {
  const total = primary.value;
  if (!total) return null;
  return trend(total.receipt_count, total.previous_receipt_count);
});
const averageTrend = computed(() => {
  const total = primary.value;
  if (!total?.previous_amount || !total.previous_receipt_count) return null;
  return trend(
    Number(total.average),
    Number(total.previous_amount) / total.previous_receipt_count,
  );
});

function money(amount: string): string {
  return formatMoney(amount, stats.data.value?.currency ?? "CZK", locale.value);
}

function formatCount(count: number): string {
  return new Intl.NumberFormat(numberLocale(locale.value)).format(count);
}

const merchantRows = computed<RankedRow[]>(() =>
  (stats.data.value?.merchants ?? []).map((merchant) => ({
    key: merchant.merchant,
    label: merchant.merchant,
    value: Number(merchant.amount),
    display: money(merchant.amount),
    detail: t("receiptCount", merchant.receipt_count),
  })),
);
const frequentRows = computed<RankedRow[]>(() =>
  (stats.data.value?.frequent_items ?? []).map((item) => ({
    key: item.name,
    label: item.name,
    value: item.receipt_count,
    display: `${formatCount(item.receipt_count)}×`,
    detail: money(item.amount),
  })),
);
const spendRows = computed<RankedRow[]>(() =>
  (stats.data.value?.top_spend_items ?? []).map((item) => ({
    key: item.name,
    label: item.name,
    value: Number(item.amount),
    display: money(item.amount),
    detail: t("receiptCount", item.receipt_count),
  })),
);

function selectPeriod(value: StatsPeriod): void {
  period.value = value;
  month.value = null;
}
</script>

<template>
  <div class="view-content">
    <header class="page-heading overview-heading">
      <div>
        <div class="eyebrow">{{ t("workspace") }}</div>
        <h1>{{ t("overview") }}</h1>
        <p>{{ t("overviewIntro") }}</p>
      </div>
      <div class="period-switch" role="group" :aria-label="t('period')">
        <button
          v-for="value in PERIODS"
          :key="value"
          type="button"
          :class="{ active: period === value }"
          :aria-pressed="period === value"
          @click="selectPeriod(value)"
        >
          {{ t(`period_${value}`) }}
        </button>
      </div>
    </header>

    <QueryState
      :pending="stats.isPending.value"
      :failed="stats.isError.value"
      @retry="stats.refetch()"
    >
      <div
        v-if="stats.data.value"
        class="dashboard"
        :class="{ updating: stats.isPlaceholderData.value }"
      >
        <div v-if="month" class="month-filter">
          <span>{{ formatLongMonth(month, locale) }}</span>
          <button
            type="button"
            :aria-label="t('showWholePeriod')"
            :title="t('showWholePeriod')"
            @click="month = null"
          >
            ×
          </button>
        </div>

        <dl class="kpi-grid">
          <div class="kpi kpi-main">
            <dt>{{ t("totalSpent") }}</dt>
            <dd>{{ primary ? money(primary.amount) : "–" }}</dd>
            <span v-if="spendTrend" class="trend" :class="`spend-${spendTrend.direction}`">
              {{ spendTrend.text }} <small>{{ comparisonLabel }}</small>
            </span>
            <span v-else-if="primary" class="trend">{{ t("noComparison") }}</span>
            <span v-if="otherTotals" class="kpi-note">
              {{ t("otherCurrencies", { amounts: otherTotals }) }}
            </span>
          </div>
          <div class="kpi">
            <dt>{{ t("purchases") }}</dt>
            <dd>{{ formatCount(primary?.receipt_count ?? 0) }}</dd>
            <span v-if="countTrend" class="trend">
              {{ countTrend.text }} <small>{{ comparisonLabel }}</small>
            </span>
          </div>
          <div class="kpi">
            <dt>{{ t("averageReceipt") }}</dt>
            <dd>{{ primary ? money(primary.average) : "–" }}</dd>
            <span v-if="averageTrend" class="trend" :class="`spend-${averageTrend.direction}`">
              {{ averageTrend.text }} <small>{{ comparisonLabel }}</small>
            </span>
          </div>
          <div class="kpi">
            <dt>{{ t("savedOnDiscounts") }}</dt>
            <dd class="saved">{{ primary ? money(primary.discount_amount) : "–" }}</dd>
          </div>
          <div class="kpi">
            <dt>{{ t("connectedAccounts") }}</dt>
            <dd>{{ formatCount(overview.data.value?.connection_count ?? 0) }}</dd>
          </div>
        </dl>

        <section class="panel">
          <div class="section-heading">
            <h2>{{ t("monthlySpending") }}</h2>
            <span class="panel-hint">{{ t("monthlySpendingHint") }}</span>
          </div>
          <MonthlySpendingChart
            v-if="stats.data.value.currency && stats.data.value.months.length"
            :months="stats.data.value.months"
            :currency="stats.data.value.currency"
            :selected="month"
            @select="month = $event"
          />
          <p v-else class="panel-empty">{{ t("noStatsData") }}</p>
        </section>

        <div class="panel-grid">
          <section class="panel">
            <div class="section-heading">
              <h2>{{ t("spendingByStore") }}</h2>
            </div>
            <RankedBars v-if="merchantRows.length" :rows="merchantRows" />
            <p v-else class="panel-empty">{{ t("noStatsData") }}</p>
          </section>
          <section class="panel">
            <div class="section-heading">
              <h2>{{ t("shoppingTimes") }}</h2>
              <span class="panel-hint">{{ t("shoppingTimesHint") }}</span>
            </div>
            <ShoppingHeatmap
              v-if="primary"
              :weekly-hours="stats.data.value.weekly_hours"
            />
            <p v-else class="panel-empty">{{ t("noStatsData") }}</p>
          </section>
          <section class="panel">
            <div class="section-heading">
              <h2>{{ t("frequentItems") }}</h2>
            </div>
            <RankedBars v-if="frequentRows.length" :rows="frequentRows" />
            <p v-else class="panel-empty">{{ t("noStatsData") }}</p>
          </section>
          <section class="panel">
            <div class="section-heading">
              <h2>{{ t("topSpendItems") }}</h2>
            </div>
            <RankedBars v-if="spendRows.length" :rows="spendRows" />
            <p v-else class="panel-empty">{{ t("noStatsData") }}</p>
          </section>
        </div>
      </div>
    </QueryState>

    <QueryState
      :pending="overview.isPending.value"
      :failed="overview.isError.value"
      @retry="overview.refetch()"
    >
      <section v-if="overview.data.value" class="ledger-section">
        <div class="section-heading">
          <h2>{{ t("latestReceipts") }}</h2>
          <RouterLink class="text-link" to="/receipts">
{{ t("viewAll") }}<AppIcon name="arrow" />
</RouterLink>
        </div>
        <ReceiptLedger :receipts="overview.data.value.latest_receipts" />
      </section>
    </QueryState>
  </div>
</template>

<style scoped>
.overview-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}
.period-switch {
  display: inline-flex;
  padding: 4px;
  gap: 2px;
  background: var(--light);
  border-radius: 8px;
}
.period-switch button {
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
  padding: 9px 14px;
  min-height: 36px;
  border-radius: 6px;
  transition:
    background 0.16s,
    color 0.16s;
}
.period-switch button:hover {
  color: var(--green);
}
.period-switch button.active {
  background: #fbfbf6;
  color: var(--green);
  box-shadow: 0 1px 3px rgba(23, 62, 52, 0.12);
}
.dashboard {
  transition: opacity 0.2s;
}
.dashboard.updating {
  opacity: 0.6;
}
.month-filter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 18px;
  padding: 4px 4px 4px 14px;
  background: var(--green);
  color: var(--paper);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  text-transform: capitalize;
  animation: arrive 0.2s ease-out;
}
.month-filter button {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  color: inherit;
  font-size: 15px;
  line-height: 1;
}
.month-filter button:hover {
  background: rgba(255, 255, 255, 0.26);
}
.kpi-grid {
  display: grid;
  grid-template-columns: 1.5fr repeat(4, 1fr);
  margin: 0 0 8px;
  padding: 26px 0 32px;
  border-top: 1px solid var(--line);
}
.kpi {
  padding: 2px 24px;
  border-right: 1px solid var(--line);
  min-width: 0;
}
.kpi:first-child {
  padding-left: 0;
}
.kpi:last-child {
  border-right: 0;
}
.kpi dt {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 14px;
}
.kpi dd {
  margin: 0;
  font-family: "Manrope Variable", sans-serif;
  font-size: 28px;
  font-weight: 500;
  letter-spacing: -1px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.kpi-main dd {
  font-size: 40px;
  letter-spacing: -1.8px;
}
.kpi dd.saved {
  color: #3f7a55;
}
.trend {
  display: block;
  margin-top: 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
.trend small {
  font-weight: 400;
  font-size: 11px;
}
.trend.spend-up {
  color: #a0533f;
}
.trend.spend-down {
  color: #3f7a55;
}
.kpi-note {
  display: block;
  margin-top: 6px;
  font-size: 11px;
  color: #8b9085;
}
.panel {
  padding: 24px 0 8px;
  border-top: 1px solid var(--line);
  margin-bottom: 32px;
  min-width: 0;
}
.panel .section-heading {
  flex-wrap: wrap;
  row-gap: 4px;
  margin-bottom: 8px;
}
.panel-hint {
  font-size: 11px;
  color: var(--muted);
}
.panel-empty {
  font-size: 13px;
  color: var(--muted);
  padding: 40px 0;
  text-align: center;
}
.panel-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 48px;
}
@media (max-width: 1400px) {
  .kpi-grid {
    grid-template-columns: repeat(4, 1fr);
    row-gap: 24px;
  }
  .kpi-main {
    grid-column: 1 / -1;
    padding: 0 0 22px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .kpi:nth-child(2) {
    padding-left: 0;
  }
  .panel-grid {
    column-gap: 32px;
  }
}
@media (max-width: 780px) {
  .overview-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 18px;
  }
  .period-switch {
    width: 100%;
  }
  .period-switch button {
    flex: 1;
    padding: 9px 6px;
  }
  .kpi-grid {
    grid-template-columns: 1fr 1fr;
  }
  .kpi:not(.kpi-main) {
    padding: 0 16px;
  }
  .kpi:nth-child(2n) {
    padding-left: 0;
  }
  .kpi:nth-child(3) {
    border-right: 0;
  }
  .kpi dd {
    font-size: 22px;
  }
  .kpi-main dd {
    font-size: 34px;
  }
  .panel-grid {
    grid-template-columns: 1fr;
  }
}
</style>
