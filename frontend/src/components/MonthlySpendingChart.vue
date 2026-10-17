<script setup lang="ts">
import type { ElementEvent } from "echarts/core";
import { computed, useTemplateRef } from "vue";
import { useI18n } from "vue-i18n";
import VChart from "vue-echarts";
import type { MonthStats } from "../api";
import { axisLabelStyle, chartColors, tooltipStyle } from "../charts";
import { formatLongMonth, formatMoney, formatMonth } from "../stats";

const props = defineProps<{
  months: MonthStats[];
  currency: string;
  selected: string | null;
}>();
const emit = defineEmits<{ select: [month: string | null] }>();

const { t, locale } = useI18n();
const chart = useTemplateRef<InstanceType<typeof VChart>>("chart");

const withYear = computed(() => {
  const years = new Set(props.months.map((month) => month.month.slice(0, 4)));
  return years.size > 1;
});

const option = computed(() => ({
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
    axisPointer: { type: "shadow", shadowStyle: { color: "rgba(23, 62, 52, 0.05)" } },
    formatter: (params: Array<{ dataIndex: number }>) => {
      const month = props.months[params[0]?.dataIndex ?? 0];
      if (!month) return "";
      return [
        `<strong>${formatLongMonth(month.month, locale.value)}</strong>`,
        `${t("chartSpending")}: <strong>${formatMoney(month.amount, props.currency, locale.value)}</strong>`,
        t("receiptCount", month.receipt_count),
      ].join("<br>");
    },
  },
  xAxis: {
    type: "category",
    data: props.months.map((month) => formatMonth(month.month, locale.value, withYear.value)),
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
      data: props.months.map((month) => ({
        value: Number(month.amount),
        itemStyle: {
          color:
            props.selected === null || props.selected === month.month
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
      data: props.months.map((month) => month.receipt_count),
    },
  ],
}));

function onClick(event: ElementEvent): void {
  const instance = chart.value;
  const point = [event.offsetX, event.offsetY];
  if (!instance?.containPixel("grid", point)) return;
  const [index] = instance.convertFromPixel({ seriesIndex: 0 }, point) as number[];
  const month = props.months[Math.round(index ?? -1)];
  if (!month) return;
  emit("select", props.selected === month.month ? null : month.month);
}
</script>

<template>
  <VChart
    ref="chart"
    class="monthly-chart"
    :option="option"
    :init-options="{ renderer: 'svg' }"
    autoresize
    @zr:click="onClick"
  />
</template>

<style scoped>
.monthly-chart {
  height: 300px;
  cursor: pointer;
}
.monthly-chart :deep(svg) {
  width: 100%;
  height: 100%;
}
@media (max-width: 780px) {
  .monthly-chart {
    height: 240px;
  }
}
</style>
