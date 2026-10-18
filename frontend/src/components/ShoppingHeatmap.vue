<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import VChart from "vue-echarts";
import { getChartTheme } from "../charts";
import { weekdayNames } from "../stats";

const props = defineProps<{ weeklyHours: number[][] }>();
const { t, locale } = useI18n();
const { axisLabelStyle, chartColors, tooltipStyle } = getChartTheme();

const hours = computed(() => {
  const active = props.weeklyHours.flatMap((day) =>
    day.flatMap((count, hour) => (count > 0 ? [hour] : [])),
  );
  const first = Math.min(7, ...active);
  const last = Math.max(21, ...active);
  return Array.from({ length: last - first + 1 }, (_, index) => first + index);
});

const option = computed(() => {
  const days = weekdayNames(locale.value);
  const firstHour = hours.value[0] ?? 0;
  const data = props.weeklyHours.flatMap((day, weekday) =>
    hours.value.map((hour) => [hour - firstHour, weekday, day[hour] ?? 0]),
  );
  return {
    animationDuration: 500,
    grid: {
      top: 4,
      right: 4,
      bottom: 24,
      left: 4,
      outerBoundsMode: "same",
      outerBoundsContain: "axisLabel",
    },
    tooltip: {
      ...tooltipStyle,
      formatter: (params: { value: number[] }) => {
        const [column = 0, weekday = 0, count = 0] = params.value;
        const hour = firstHour + column;
        return `<strong>${days[weekday] ?? ""} ${hour}:00–${hour + 1}:00</strong><br>${t("receiptCount", count)}`;
      },
    },
    xAxis: {
      type: "category",
      data: hours.value.map(String),
      splitArea: { show: false },
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { ...axisLabelStyle, interval: 1 },
    },
    yAxis: {
      type: "category",
      data: days,
      inverse: true,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: axisLabelStyle,
    },
    visualMap: {
      show: false,
      min: 0,
      max: Math.max(1, ...data.map((cell) => cell[2] ?? 0)),
      inRange: { color: [chartColors.heatLow, chartColors.sage, chartColors.green] },
    },
    series: [
      {
        type: "heatmap",
        data,
        itemStyle: { borderColor: chartColors.paper, borderWidth: 3, borderRadius: 4 },
        emphasis: { itemStyle: { borderColor: chartColors.green, borderWidth: 1 } },
      },
    ],
  };
});
</script>

<template>
  <VChart
    class="heatmap-chart"
    :option="option"
    :init-options="{ renderer: 'svg' }"
    autoresize
  />
</template>

<style scoped>
.heatmap-chart {
  height: 250px;
}
.heatmap-chart :deep(svg) {
  width: 100%;
  height: 100%;
}
</style>
