<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import type { RouteLocationRaw } from "vue-router";
import VChart from "vue-echarts";
import type { CategoryStats } from "../api";
import { escapeHtml, getChartTheme } from "../charts";
import { formatMoney, numberLocale } from "../stats";

interface Slice {
  label: string;
  color: string;
  purchases: number;
  amount: number;
  to: RouteLocationRaw;
}

interface Row {
  key: string;
  label: string;
  color: string;
  purchases: number;
  amount: string;
  to: RouteLocationRaw;
}

// More categories than this get larger charts stacked beside the table.
const LARGE_FROM = 7;

/** `slices` is the segment count; when categories exceed it, the last segment folds the rest. */
const props = defineProps<{ categories: CategoryStats[]; currency: string; slices: number }>();
const { t, locale } = useI18n();
const router = useRouter();

function categoryRoute(category: CategoryStats): RouteLocationRaw {
  return category.category_id === null
    ? { name: "categories", query: { category: "uncategorized" } }
    : { name: "categories", query: { category: category.category_id, name: category.name ?? "" } };
}

// Colors follow the categories ranked by spending, so one category keeps its color in both charts.
const ranked = computed(() => {
  const { categoryColors, chartColors } = getChartTheme();
  const named = props.categories.filter((category) => category.category_id !== null);
  const uncategorized = props.categories.find((category) => category.category_id === null);
  const limit = Math.min(props.slices, categoryColors.length);
  const shown = named.length <= limit ? named.length : Math.min(props.slices - 1, categoryColors.length);
  const colorOf = (index: number): string => (index < shown ? categoryColors[index] : undefined) ?? chartColors.faded;
  const label = (category: CategoryStats): string => category.name ?? t("uncategorized");
  const rows: Row[] = [...named, ...(uncategorized ? [uncategorized] : [])].map((category, index) => ({
    key: category.category_id ?? "uncategorized",
    label: label(category),
    color: category.category_id === null ? chartColors.uncategorized : colorOf(index),
    purchases: category.purchase_count,
    amount: category.amount,
    to: categoryRoute(category),
  }));
  const slices: Slice[] = named.slice(0, shown).map((category, index) => ({
    label: label(category),
    color: colorOf(index),
    purchases: category.purchase_count,
    amount: Number(category.amount),
    to: categoryRoute(category),
  }));
  const rest = named.slice(shown);
  if (rest.length) {
    slices.push({
      label: t("otherCategories", rest.length),
      color: chartColors.faded,
      purchases: rest.reduce((sum, category) => sum + category.purchase_count, 0),
      amount: rest.reduce((sum, category) => sum + Number(category.amount), 0),
      to: { name: "categories" },
    });
  }
  if (uncategorized) {
    slices.push({
      label: label(uncategorized),
      color: chartColors.uncategorized,
      purchases: uncategorized.purchase_count,
      amount: Number(uncategorized.amount),
      to: categoryRoute(uncategorized),
    });
  }
  return { rows, slices };
});

const large = computed(() => ranked.value.rows.length >= LARGE_FROM);
const totalPurchases = computed(() => ranked.value.slices.reduce((sum, slice) => sum + slice.purchases, 0));
const totalAmount = computed(() => ranked.value.slices.reduce((sum, slice) => sum + slice.amount, 0));

function count(value: number): string {
  return new Intl.NumberFormat(numberLocale(locale.value)).format(value);
}

function money(value: number | string): string {
  return formatMoney(value, props.currency, locale.value);
}

function share(value: number, total: number): string {
  return new Intl.NumberFormat(numberLocale(locale.value), { style: "percent", maximumFractionDigits: 0 })
    .format(total > 0 ? value / total : 0);
}

function donut(measure: "purchases" | "amount") {
  const { chartColors, tooltipStyle } = getChartTheme();
  const total = measure === "purchases" ? totalPurchases.value : totalAmount.value;
  return {
    animationDuration: 500,
    tooltip: {
      ...tooltipStyle,
      trigger: "item",
      formatter: (params: { name: string; value: number }) => {
        const value = measure === "purchases" ? `${count(params.value)}×` : money(params.value);
        return `<strong>${escapeHtml(params.name)}</strong><br>${value} · ${share(params.value, total)}`;
      },
    },
    series: [
      {
        type: "pie",
        radius: ["62%", "86%"],
        label: { show: false },
        labelLine: { show: false },
        cursor: "pointer",
        // The surface-colored border is the 2px gap between neighboring segments.
        itemStyle: { borderColor: chartColors.page, borderWidth: 2, borderRadius: 4 },
        emphasis: { scale: true, scaleSize: 4 },
        data: ranked.value.slices.map((slice) => ({
          name: slice.label,
          value: slice[measure],
          itemStyle: { color: slice.color },
        })),
      },
    ],
  };
}

const charts = computed(() => [
  { key: "purchases", title: t("byPurchases"), center: `${count(totalPurchases.value)}×`, note: t("purchasedItems"), option: donut("purchases") },
  { key: "amount", title: t("bySpending"), center: money(totalAmount.value), note: t("totalSpent"), option: donut("amount") },
]);

function openSlice(event: { dataIndex: number }): void {
  const slice = ranked.value.slices[event.dataIndex];
  if (slice) void router.push(slice.to);
}
</script>

<template>
  <div class="category-breakdown" :class="{ large }">
    <div class="category-charts">
      <figure v-for="chart in charts" :key="chart.key" class="category-chart">
        <figcaption>{{ chart.title }}</figcaption>
        <div class="donut">
          <VChart class="donut-chart" :option="chart.option" :init-options="{ renderer: 'svg' }" autoresize @click="openSlice" />
          <div class="donut-center" aria-hidden="true">
            <strong>{{ chart.center }}</strong>
            <span>{{ chart.note }}</span>
          </div>
        </div>
      </figure>
    </div>
    <table class="category-table">
      <thead>
        <tr>
          <th scope="col">{{ t("category") }}</th>
          <th scope="col" class="amount">{{ t("purchases") }}</th>
          <th scope="col" class="amount">{{ t("spent") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in ranked.rows" :key="row.key">
          <th scope="row">
            <span class="swatch" :style="{ background: row.color }" aria-hidden="true" />
            <RouterLink class="category-link" :to="row.to">{{ row.label }}</RouterLink>
          </th>
          <td class="amount">
            {{ count(row.purchases) }}× <small>{{ share(row.purchases, totalPurchases) }}</small>
          </td>
          <td class="amount strong">
            {{ money(row.amount) }} <small>{{ share(Number(row.amount), totalAmount) }}</small>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.category-breakdown { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); align-items: center; gap: 40px; }
.category-breakdown.large { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); align-items: start; }
.category-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.large .category-charts { grid-template-columns: 1fr; gap: 32px; position: sticky; top: 24px; }
.category-chart { margin: 0; min-width: 0; }
.category-chart figcaption { margin-bottom: 8px; font-size: 12px; color: var(--text-muted); text-align: center; }
.donut { position: relative; }
.donut-chart { height: 230px; }
.large .donut-chart { height: 340px; }
.donut-chart :deep(svg) { width: 100%; height: 100%; }
.donut-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; text-align: center; }
.donut-center strong { font-family: "Manrope Variable", sans-serif; font-size: 18px; font-weight: 600; letter-spacing: -0.5px; font-variant-numeric: tabular-nums; }
.large .donut-center strong { font-size: 24px; }
.donut-center span { margin-top: 2px; font-size: 11px; color: var(--text-muted); }
.category-table { width: 100%; }
.category-table th[scope="row"] { font-weight: 500; color: var(--text); white-space: normal; }
.category-table td,
.category-table th { padding: 10px 8px; font-size: 13px; }
.category-table td:first-child,
.category-table th:first-child { padding-left: 0; }
.category-table td:last-child,
.category-table th:last-child { padding-right: 0; }
.category-table thead th { font-size: 11px; }
.category-table small { margin-left: 4px; font-size: 11px; color: var(--text-muted); }
.category-link { color: inherit; }
.category-link:hover { color: var(--accent); text-decoration: underline; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; }
.swatch { display: inline-block; width: 10px; height: 10px; margin-right: 10px; border-radius: 3px; vertical-align: -1px; }
@media (max-width: 1100px) {
  .category-breakdown,
  .category-breakdown.large { grid-template-columns: 1fr; gap: 16px; }
  .large .category-charts { grid-template-columns: 1fr 1fr; position: static; }
  .large .donut-chart { height: 260px; }
}
@media (max-width: 520px) { .category-charts, .large .category-charts { grid-template-columns: 1fr; } }
</style>
