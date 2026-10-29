<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import VChart from "vue-echarts";
import type { ItemPurchase } from "../api";
import { getChartTheme, stepPoints } from "../charts";
import type { StepPoint } from "../charts";
import { formatMoney, numberLocale } from "../stats";

const props = defineProps<{ purchases: ItemPurchase[]; currency: string }>();
const { locale } = useI18n();

const option = computed(() => {
  const { axisLabelStyle, chartColors, tooltipStyle } = getChartTheme();
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
      formatter: (params: Array<{ data: StepPoint }>) => {
        const point = points[params[0]?.data.purchase ?? 0];
        if (!point) return "";
        return [
          `<strong>${formatMoney(point.unit_price, props.currency, locale.value)}</strong>`,
          dates.format(new Date(point.purchased_at)),
        ].join("<br>");
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: chartColors.border } },
      axisTick: { show: false },
      axisLabel: { ...axisLabelStyle, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      splitNumber: 4,
      splitLine: { lineStyle: { color: chartColors.border, type: "dashed" } },
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
        lineStyle: { width: 2, color: chartColors.accent },
        itemStyle: { color: chartColors.accent, borderColor: chartColors.page, borderWidth: 2 },
        areaStyle: { color: chartColors.area },
        data: stepPoints(points),
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
