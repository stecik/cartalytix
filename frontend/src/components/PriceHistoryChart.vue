<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import VChart from "vue-echarts";
import type { ItemPurchase } from "../api";
import { getChartTheme } from "../charts";
import { formatMoney, numberLocale } from "../stats";

const props = defineProps<{ purchases: ItemPurchase[]; currency: string }>();
const { locale } = useI18n();
const { axisLabelStyle, chartColors, tooltipStyle } = getChartTheme();

const option = computed(() => {
  const points = [...props.purchases].reverse();
  const dates = new Intl.DateTimeFormat(numberLocale(locale.value), {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  return {
    animationDuration: 500,
    grid: {
      top: 16,
      right: 12,
      bottom: 28,
      left: 8,
      outerBoundsMode: "same",
      outerBoundsContain: "axisLabel",
    },
    tooltip: {
      ...tooltipStyle,
      trigger: "axis",
      formatter: (params: Array<{ dataIndex: number }>) => {
        const point = points[params[0]?.dataIndex ?? 0];
        if (!point) return "";
        return [
          `<strong>${formatMoney(point.unit_price, props.currency, locale.value)}</strong>`,
          dates.format(new Date(point.purchased_at)),
        ].join("<br>");
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { show: false },
      axisLabel: { ...axisLabelStyle, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      splitNumber: 4,
      splitLine: { lineStyle: { color: chartColors.line, type: "dashed" } },
      axisLabel: {
        ...axisLabelStyle,
        formatter: (value: number) => formatMoney(value, props.currency, locale.value),
      },
    },
    series: [
      {
        type: "line",
        step: "end",
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2, color: chartColors.green },
        itemStyle: { color: chartColors.green, borderColor: chartColors.paper, borderWidth: 2 },
        areaStyle: { color: chartColors.area },
        data: points.map((point) => [point.purchased_at, Number(point.unit_price)]),
      },
    ],
  };
});
</script>

<template>
  <VChart class="price-chart" :option="option" :init-options="{ renderer: 'svg' }" autoresize />
</template>

<style scoped>
.price-chart {
  height: 260px;
}
.price-chart :deep(svg) {
  width: 100%;
  height: 100%;
}
</style>
