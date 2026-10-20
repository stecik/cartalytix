<script setup lang="ts">
import type { ElementEvent } from "echarts/core";
import { computed, useTemplateRef } from "vue";
import { useI18n } from "vue-i18n";
import VChart from "vue-echarts";
import type { BucketStats, StatsGranularity } from "../api";
import { escapeHtml, getChartTheme } from "../charts";
import { formatDay, formatLongDay, formatLongMonth, formatMoney, formatMonth } from "../stats";

const props = defineProps<{
  buckets: BucketStats[];
  granularity: StatsGranularity;
  currency: string;
  selected: string | null;
}>();
const emit = defineEmits<{ select: [bucket: string | null] }>();

const { t, locale } = useI18n();
const chart = useTemplateRef<InstanceType<typeof VChart>>("chart");

const withYear = computed(() => {
  const years = new Set(props.buckets.map((bucket) => bucket.key.slice(0, 4)));
  return years.size > 1;
});

function label(key: string): string {
  return props.granularity === "day"
    ? formatDay(key, locale.value)
    : formatMonth(key, locale.value, withYear.value);
}

function longLabel(key: string): string {
  return props.granularity === "day"
    ? formatLongDay(key, locale.value)
    : formatLongMonth(key, locale.value);
}

const option = computed(() => {
  const { axisLabelStyle, chartColors, tooltipStyle } = getChartTheme();
  return {
    animationDuration: 500,
    grid: {
      top: 24,
      right: 8,
      bottom: 28,
      left: 8,
      outerBoundsMode: "same",
      outerBoundsContain: "axisLabel",
    },
    tooltip: {
      ...tooltipStyle,
      trigger: "axis",
      axisPointer: { type: "shadow", shadowStyle: { color: chartColors.pointer } },
      formatter: (params: Array<{ dataIndex: number }>) => {
        const bucket = props.buckets[params[0]?.dataIndex ?? 0];
        if (!bucket) return "";
        return [
          `<strong>${longLabel(bucket.key)}</strong>`,
          ...bucket.merchants.map((merchant) =>
            `${escapeHtml(merchant.merchant)}: <strong>${formatMoney(merchant.amount, props.currency, locale.value)}</strong> · ${t("receiptCount", merchant.receipt_count)}`),
          `${t("totalSpent")}: <strong>${formatMoney(bucket.amount, props.currency, locale.value)}</strong>`,
          t("receiptCount", bucket.receipt_count),
        ].join("<br>");
      },
    },
    xAxis: {
      type: "category",
      data: props.buckets.map((bucket) => label(bucket.key)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisLabel: { ...axisLabelStyle, hideOverlap: true },
    },
    yAxis: [
      {
        type: "value",
        splitNumber: 4,
        splitLine: { lineStyle: { color: chartColors.line, type: "dashed" } },
        axisLabel: {
          ...axisLabelStyle,
          formatter: (value: number) => formatMoney(value, props.currency, locale.value, true),
        },
      },
      { type: "value", show: false, splitLine: { show: false } },
    ],
    series: [
      {
        name: t("chartSpending"),
        type: "bar",
        barMaxWidth: 38,
        data: props.buckets.map((bucket) => ({
          value: Number(bucket.amount),
          itemStyle: {
            color:
              props.selected === null || props.selected === bucket.key
                ? chartColors.green
                : chartColors.faded,
            borderRadius: [4, 4, 0, 0],
          },
        })),
        emphasis: { itemStyle: { color: chartColors.greenHover } },
      },
      {
        name: t("purchases"),
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2, color: chartColors.sage },
        itemStyle: { color: chartColors.sage, borderColor: chartColors.paper, borderWidth: 2 },
        data: props.buckets.map((bucket) => bucket.receipt_count),
      },
    ],
  };
});

function onClick(event: ElementEvent): void {
  const instance = chart.value;
  const point = [event.offsetX, event.offsetY];
  if (!instance?.containPixel("grid", point)) return;
  const [index] = instance.convertFromPixel({ seriesIndex: 0 }, point) as number[];
  const bucket = props.buckets[Math.round(index ?? -1)];
  if (!bucket) return;
  emit("select", props.selected === bucket.key ? null : bucket.key);
}
</script>

<template>
  <VChart
    ref="chart"
    class="spending-chart"
    :option="option"
    :init-options="{ renderer: 'svg' }"
    autoresize
    @zr:click="onClick"
  />
</template>

<style scoped>
.spending-chart {
  height: 300px;
  cursor: pointer;
}
.spending-chart :deep(svg) {
  width: 100%;
  height: 100%;
}
@media (max-width: 780px) {
  .spending-chart {
    height: 240px;
  }
}
</style>
