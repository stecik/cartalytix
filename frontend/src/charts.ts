import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
} from "echarts/components";
import type { TooltipComponentOption } from "echarts/components";
import { use } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { resolvedTheme } from "./theme";

use([
  SVGRenderer,
  BarChart,
  LineChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapContinuousComponent,
]);

interface ChartTheme {
  chartColors: Record<
    "green" | "greenHover" | "faded" | "sage" | "muted" | "line" | "paper" |
    "heatLow" | "area" | "pointer", string
  >;
  tooltipStyle: TooltipComponentOption;
  axisLabelStyle: { color: string; fontFamily: string; fontSize: number };
}

export function getChartTheme(): ChartTheme {
  // Chart options track the current palette when evaluated in a computed value.
  void resolvedTheme.value;
  const style = getComputedStyle(document.documentElement);
  function color(name: string): string {
    const value = style.getPropertyValue(name).trim();
    if (!value) throw new Error(`Missing chart color: ${name}`);
    return value;
  }
  const chartColors = {
    green: color("--green"),
    greenHover: color("--green-hover"),
    faded: color("--chart-muted"),
    sage: color("--focus"),
    muted: color("--muted"),
    line: color("--line"),
    paper: color("--paper"),
    heatLow: color("--surface-search-hover"),
    area: color("--shadow-green-06"),
    pointer: color("--shadow-green-05"),
  };
  return {
    chartColors,
    tooltipStyle: {
      backgroundColor: color("--surface-input"),
      borderColor: chartColors.line,
      borderWidth: 1,
      padding: [10, 14],
      textStyle: { color: color("--text"), fontFamily: "DM Sans Variable, sans-serif", fontSize: 12 },
      extraCssText: `box-shadow: 0 8px 24px ${color("--shadow-green-12")}; border-radius: 6px;`,
    },
    axisLabelStyle: {
      color: chartColors.muted,
      fontFamily: "DM Sans Variable, sans-serif",
      fontSize: 11,
    },
  };
}

export interface StepPoint {
  value: [number, number];
  purchase: number;
  symbol?: "none";
}

/**
 * A price step lasts until the next purchase. The axis tooltip snaps to the nearest point, so an
 * invisible point closes every step and hovering a step reports its own price, not the next one.
 */
export function stepPoints(points: Array<{ purchased_at: string; unit_price: string }>): StepPoint[] {
  return points.flatMap((point, index) => {
    const time = new Date(point.purchased_at).getTime();
    const price = Number(point.unit_price);
    const own: StepPoint = { value: [time, price], purchase: index };
    const next = points[index + 1];
    const end = next ? new Date(next.purchased_at).getTime() - 1 : time;
    return end > time ? [own, { value: [end, price], purchase: index, symbol: "none" }] : [own];
  });
}